"""The website's TradeHub account API: connect a read-only exchange key, see what it holds and
every fill it made. Every route takes the website user id and only ever reads that user's connections."""

import time
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.core.dependencies import db_getter

from ..movie.auth import verify_site_token
from ..movie.schema import SiteUserId
from . import job, service
from .binance import KeyRejected

Db = Annotated[AsyncSession, Depends(db_getter)]
Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9_]{3,32}$")]
Product = Literal["spot", "coinm"]

TradeWebsiteRouter = APIRouter(prefix="/website/trade", tags=["网站 TradeHub"], dependencies=[Depends(verify_site_token), Depends(job.ensure_loop)])


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: Literal["binance"] = "binance"
    label: str = Field(default="", max_length=64)
    apiKey: str = Field(min_length=16, max_length=512, pattern=r"^[A-Za-z0-9+/=_\-]+$")
    apiSecret: str = Field(min_length=16, max_length=4096)


class TrackedSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: Product
    symbol: Symbol


class TrackedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[TrackedSymbol] = Field(default_factory=list, max_length=50)
    remove: list[TrackedSymbol] = Field(default_factory=list, max_length=50)


def _dt(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).replace(tzinfo=None)


async def _connection(db: AsyncSession, site_user_id: str, connection_id: int):
    conn = await service.connection(db, site_user_id, connection_id)
    if conn is None:
        raise HTTPException(404, "notConnected")
    return conn


_ip: tuple[str, float] | None = None


@TradeWebsiteRouter.get("/ip")
async def get_ip():
    """This host's public address, for the user to allow on their key (cached an hour)."""
    global _ip
    if _ip is None or time.monotonic() - _ip[1] > 3600:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                response = await http.get("https://api.ipify.org", params={"format": "json"})
                response.raise_for_status()
                _ip = (response.json()["ip"], time.monotonic())
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise HTTPException(503, "ipUnavailable") from error
    return SuccessResponse(data={"ip": _ip[0]})


@TradeWebsiteRouter.get("/users/{site_user_id}/connections")
async def get_connections(site_user_id: SiteUserId, db: Db):
    rows = await service.connections(db, site_user_id)
    return SuccessResponse(data=[service.connection_out(c, await service.syncs(db, c.id)) for c in rows])


@TradeWebsiteRouter.post("/users/{site_user_id}/connections")
async def post_connection(site_user_id: SiteUserId, data: ConnectIn, db: Db):
    """Checks the key with the exchange and starts reading. Refused when it can withdraw
    (keyWithdrawals), can't read (keyReading) or doesn't work (keyInvalid)."""
    if len(await service.connections(db, site_user_id)) >= 5:
        raise HTTPException(400, "tooMany")
    try:
        conn = await service.connect(db, site_user_id, data.label, data.apiKey, data.apiSecret)
    except KeyRejected as error:
        reason = str(error)
        raise HTTPException(400, "keyWithdrawals" if reason == "withdrawals" else "keyReading" if reason == "reading" else "keyInvalid") from error
    return SuccessResponse(data=service.connection_out(conn, []))


@TradeWebsiteRouter.delete("/users/{site_user_id}/connections/{connection_id}")
async def delete_connection(site_user_id: SiteUserId, connection_id: int, db: Db):
    await service.disconnect(db, await _connection(db, site_user_id, connection_id))
    return SuccessResponse(data={"connected": False})


@TradeWebsiteRouter.post("/users/{site_user_id}/connections/{connection_id}/refresh")
async def post_refresh(site_user_id: SiteUserId, connection_id: int, db: Db):
    """Asks for the balances and new fills at the next tick instead of waiting the usual minutes."""
    conn = await _connection(db, site_user_id, connection_id)
    now = service.utcnow()
    if conn.status == "error":
        conn.status = "ok"
    if conn.permissions.get("coinmUnavailable"):
        conn.permissions = {k: v for k, v in conn.permissions.items() if k != "coinmUnavailable"}
    conn.next_check_at = now
    for s in await service.syncs(db, conn.id):
        s.next_at = now
    return SuccessResponse(data=service.connection_out(conn, await service.syncs(db, conn.id)))


@TradeWebsiteRouter.put("/users/{site_user_id}/connections/{connection_id}/tracked")
async def put_tracked(site_user_id: SiteUserId, connection_id: int, data: TrackedIn, db: Db):
    """Symbols followed by hand (closed positions the balances no longer show)."""
    conn = await _connection(db, site_user_id, connection_id)
    try:
        await service.set_tracked(db, conn, [t.model_dump() for t in data.add], [t.model_dump() for t in data.remove])
    except KeyRejected as error:
        raise HTTPException(400, "unknownSymbol" if str(error) == "unknownSymbol" else "keyInvalid") from error
    return SuccessResponse(data=service.connection_out(conn, await service.syncs(db, conn.id)))


@TradeWebsiteRouter.get("/users/{site_user_id}/connections/{connection_id}/balances")
async def get_balances(site_user_id: SiteUserId, connection_id: int, db: Db):
    conn = await _connection(db, site_user_id, connection_id)
    return SuccessResponse(data={"balances": await service.balances(db, conn.id), "positions": await service.positions(db, conn.id)})


@TradeWebsiteRouter.get("/users/{site_user_id}/connections/{connection_id}/fills")
async def get_fills(
    site_user_id: SiteUserId,
    connection_id: int,
    db: Db,
    product: Product | None = None,
    symbol: Symbol | None = None,
    since: Annotated[int | None, Query(ge=0)] = None,
    until: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
):
    """Fills in a time range (milliseconds), oldest first; the newest `limit` when there are more."""
    conn = await _connection(db, site_user_id, connection_id)
    rows = await service.fills(db, conn.id, product, symbol, _dt(since), _dt(until), limit)
    return SuccessResponse(data={"fills": [service.fill_out(f) for f in rows], "more": len(rows) >= limit})


@TradeWebsiteRouter.get("/users/{site_user_id}/connections/{connection_id}/income")
async def get_income(
    site_user_id: SiteUserId,
    connection_id: int,
    db: Db,
    since: Annotated[int | None, Query(ge=0)] = None,
    until: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
):
    """Futures income (funding, realized profit, commissions, transfers) in a time range, oldest first."""
    conn = await _connection(db, site_user_id, connection_id)
    rows = await service.income(db, conn.id, _dt(since), _dt(until), limit)
    return SuccessResponse(data={"income": rows, "more": len(rows) >= limit})


@TradeWebsiteRouter.get("/users/{site_user_id}/connections/{connection_id}/reconcile")
async def get_reconcile(site_user_id: SiteUserId, connection_id: int, db: Db):
    """Per asset: what the spot fills add up to, what the wallets hold, and the difference."""
    conn = await _connection(db, site_user_id, connection_id)
    return SuccessResponse(data=await service.reconcile(db, conn.id))
