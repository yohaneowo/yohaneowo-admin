from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.core.dependencies import db_getter

from ..internal_auth import verify_bot_token
from ..movie.auth import verify_site_token
from ..movie.schema import DiscordId, SiteUserId
from . import guests, job, samples, service
from .model import DotaGuestModel, DotaProfileModel

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


class GuestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestedBy: SiteUserId
    # A pro (on a team in the website's GOAT): never dropped for going unseen.
    keep: bool = False


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


@DotaWebsiteRouter.get("/users/{site_user_id}/details")
async def get_details(site_user_id: SiteUserId, db: Db, limit: Annotated[int, Query(ge=0, le=100_000)] = 100):
    """The newest stored ranked matches with economy and IMP details (limit 0: all of them)."""
    row = await db.get(DotaProfileModel, site_user_id)
    if row is None:
        raise HTTPException(404, "notLinked")
    matches = await service.recent_details(db, row.account_id, limit or None)
    return SuccessResponse(data={"historyDone": row.backfill_done, "matches": [service.details_out(m) for m in matches]})


@DotaWebsiteRouter.get("/users/{site_user_id}/bracket")
async def get_bracket(site_user_id: SiteUserId, db: Db, limit: Annotated[int, Query(ge=0, le=100_000)] = 100):
    """The other nine players of the newest stored ranked matches: the player's bracket (limit 0: all)."""
    row = await db.get(DotaProfileModel, site_user_id)
    if row is None:
        raise HTTPException(404, "notLinked")
    matches = await service.recent_bracket(db, row.account_id, limit or None)
    return SuccessResponse(data={"matches": [service.bracket_out(m) for m in matches]})


@DotaWebsiteRouter.get("/samples/{hero_id}/{tier}")
async def get_sample(
    hero_id: Annotated[int, Path(ge=1, le=1000)],
    tier: Annotated[int, Path(ge=1, le=8)],
    db: Db,
    limit: Annotated[int, Query(ge=1, le=samples.KEEP)] = samples.TARGET,
):
    """A hero's ranked matches at a rank tier (medal 1–8). Asking keeps it collected in the
    background; while it's short of the target, what's there so far."""
    row = await samples.want(db, hero_id, tier)
    rows = await samples.stored(db, hero_id, tier, limit)
    return SuccessResponse(
        data={
            "count": await samples.usable_count(db, hero_id, tier),
            "target": samples.TARGET,
            "filledAt": int(row.filled_at.replace(tzinfo=UTC).timestamp()) if row.filled_at else None,
            "matches": [samples.sample_out(r) for r in rows],
        }
    )


AccountId = Annotated[int, Path(ge=1, le=4_294_967_295)]


@DotaWebsiteRouter.post("/guests/{account_id}")
async def post_guest(account_id: AccountId, data: GuestIn, db: Db):
    """A signed-in user confirmed reading this account's whole ranked history: queue it (again: its
    status). 409 linked (it's a linked account), 429 tooMany (the user has PER_USER waiting)."""
    result = await guests.request(db, account_id, data.requestedBy, service.utcnow(), keep=data.keep)
    if result == "linked":
        raise HTTPException(409, "linked")
    if result == "tooMany":
        raise HTTPException(429, "tooMany")
    return SuccessResponse(data=await guests.status(db, result))


@DotaWebsiteRouter.get("/guests/{account_id}")
async def get_guest(account_id: AccountId, db: Db):
    """Where a guest's history stands (404 notQueued)."""
    row = await db.get(DotaGuestModel, account_id)
    if row is None:
        raise HTTPException(404, "notQueued")
    return SuccessResponse(data=await guests.status(db, row))


@DotaWebsiteRouter.get("/guests")
async def list_guests(db: Db, requestedBy: Annotated[str, Query(min_length=1, max_length=64)]):
    """The guests a website user asked for, newest first (for its notifications)."""
    rows = await db.scalars(select(DotaGuestModel).where(DotaGuestModel.requested_by == requestedBy).order_by(DotaGuestModel.requested_at.desc()).limit(20))
    return SuccessResponse(data=[await guests.status(db, r) for r in rows])


async def _guest_seen(db: AsyncSession, account_id: int) -> DotaGuestModel:
    row = await db.get(DotaGuestModel, account_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "notQueued")
    guests.seen(row, service.utcnow())
    return row


@DotaWebsiteRouter.get("/accounts/{account_id}/deaths")
async def get_guest_deaths(account_id: AccountId, db: Db, limit: Annotated[int, Query(ge=0, le=100_000)] = 50):
    """A guest's stored ranked matches with their deaths (limit 0: all), as for a linked account."""
    row = await _guest_seen(db, account_id)
    matches = await service.recent_deaths(db, account_id, limit or None)
    return SuccessResponse(data={"historyDone": row.backfill_done, "matches": [service.match_out(m) for m in matches]})


@DotaWebsiteRouter.get("/accounts/{account_id}/details")
async def get_guest_details(account_id: AccountId, db: Db, limit: Annotated[int, Query(ge=0, le=100_000)] = 100):
    """A guest's stored ranked matches with economy and IMP details (limit 0: all)."""
    row = await _guest_seen(db, account_id)
    matches = await service.recent_details(db, account_id, limit or None)
    return SuccessResponse(data={"historyDone": row.backfill_done, "matches": [service.details_out(m) for m in matches]})


@DotaInternalRouter.get("/notifications")
async def get_notifications(db: Db):
    """Ranked matches whose death-map DM the bot hasn't sent yet."""
    return SuccessResponse(data=await service.pending(db))


@DotaInternalRouter.post("/notifications/{account_id}/{match_id}/sent")
async def post_sent(account_id: int, match_id: int, db: Db):
    if not await service.mark_sent(db, account_id, match_id):
        raise HTTPException(404, "notFound")
    return SuccessResponse(data={"sent": True})
