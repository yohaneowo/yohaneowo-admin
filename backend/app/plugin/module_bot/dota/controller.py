from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.core.dependencies import db_getter

from ..internal_auth import verify_bot_token
from ..movie.auth import verify_site_token
from ..movie.schema import DiscordId, SiteUserId
from . import job, service
from .model import DotaProfileModel

Db = Annotated[AsyncSession, Depends(db_getter)]

# The bot: matches waiting for their death-map DM.
DotaInternalRouter = APIRouter(prefix="/internal/dota", tags=["DotaHub 内部接口"], dependencies=[Depends(verify_bot_token), Depends(job.ensure_loop)])
# The website: the linked Steam account, the DM setting and the stored deaths.
DotaWebsiteRouter = APIRouter(prefix="/website/dota", tags=["网站 DotaHub"], dependencies=[Depends(verify_site_token), Depends(job.ensure_loop)])


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steamId: str = Field(pattern=r"^[0-9]{17}$")
    accountId: int = Field(ge=1, le=4_294_967_295)
    discordId: DiscordId | None = None


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notify: bool


def _status(row: DotaProfileModel | None, stored: int) -> dict:
    if row is None:
        return {"linked": False}
    return {
        "linked": True,
        "accountId": row.account_id,
        "notify": row.notify,
        "discordLinked": bool(row.discord_id),
        "historyDone": row.backfill_done,
        "historyRead": row.backfill_skip,
        "stored": stored,
    }


@DotaWebsiteRouter.put("/users/{site_user_id}/link")
async def put_link(site_user_id: SiteUserId, data: LinkIn, db: Db):
    """The website linked Steam: start checking this account (and filling in its history)."""
    row = await service.link(db, site_user_id, data.steamId, data.accountId, data.discordId)
    return SuccessResponse(data=_status(row, await service.stored_count(db, row.account_id)))


@DotaWebsiteRouter.delete("/users/{site_user_id}/link")
async def delete_link(site_user_id: SiteUserId, db: Db):
    """Unlinked: stop checking, and forget the stored deaths."""
    await service.unlink(db, site_user_id)
    return SuccessResponse(data={"linked": False})


@DotaWebsiteRouter.get("/users/{site_user_id}")
async def get_status(site_user_id: SiteUserId, db: Db, discordId: DiscordId | None = None):
    """Link status; a Discord id passed along (linked on the website later) is remembered for DMs."""
    row = await db.get(DotaProfileModel, site_user_id)
    if row is not None and discordId and row.discord_id != discordId:
        row.discord_id = discordId
    return SuccessResponse(data=_status(row, await service.stored_count(db, row.account_id) if row else 0))


@DotaWebsiteRouter.put("/users/{site_user_id}/settings")
async def put_settings(site_user_id: SiteUserId, data: SettingsIn, db: Db):
    row = await db.get(DotaProfileModel, site_user_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "notLinked")
    row.notify = data.notify
    return SuccessResponse(data={"notify": row.notify})


@DotaWebsiteRouter.get("/users/{site_user_id}/deaths")
async def get_deaths(site_user_id: SiteUserId, db: Db, limit: Annotated[int, Query(ge=0, le=100_000)] = 50):
    """The newest stored ranked matches with their deaths (limit 0: all of them)."""
    row = await db.get(DotaProfileModel, site_user_id)
    if row is None:
        raise HTTPException(404, "notLinked")
    matches = await service.recent_deaths(db, row.account_id, limit or None)
    return SuccessResponse(data={"historyDone": row.backfill_done, "matches": [service.match_out(m) for m in matches]})


@DotaInternalRouter.get("/notifications")
async def get_notifications(db: Db):
    """Ranked matches whose death-map DM the bot hasn't sent yet."""
    return SuccessResponse(data=await service.pending(db))


@DotaInternalRouter.post("/notifications/{account_id}/{match_id}/sent")
async def post_sent(account_id: int, match_id: int, db: Db):
    if not await service.mark_sent(db, account_id, match_id):
        raise HTTPException(404, "notFound")
    return SuccessResponse(data={"sent": True})
