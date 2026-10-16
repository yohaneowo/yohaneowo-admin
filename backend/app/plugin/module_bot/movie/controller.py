from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter

from ..internal_auth import verify_bot_token
from . import people, service
from .auth import verify_site_token
from .model import MovieSubscriberModel
from .schema import CacheBatch, CacheKeys, DiscordId, RatingsIn, RecordPatch, SourceRequest, SubscriptionIn, WikimediaRequest
from .sources import omdb, tmdb, trakt, wikipedia
from .sources.base import SourceUnavailable
from .sync import job

Db = Annotated[AsyncSession, Depends(db_getter, scope="function")]
CacheNamespace = Literal["tmdb", "embeddings", "wikipedia", "tmdb_person", "tmdb_collection"]
# job.ensure_loop starts the statistics sync loop in this process the first time any movie route is used.
MovieInternalRouter = APIRouter(prefix="/internal/movies", tags=["电影内部接口"], dependencies=[Depends(verify_bot_token), Depends(job.ensure_loop)])
MovieWebsiteRouter = APIRouter(prefix="/website/movies", tags=["网站观影统计"], dependencies=[Depends(verify_site_token), Depends(job.ensure_loop)])
MovieRouter = APIRouter(prefix="/movies", tags=["电影订阅管理"], dependencies=[Depends(job.ensure_loop)])


@MovieInternalRouter.get("/subscribers")
async def list_subscribers(db: Db):
    rows = await db.scalars(select(MovieSubscriberModel).where(MovieSubscriberModel.active.is_(True)))
    return SuccessResponse(data=[service.subscription_out(row) for row in rows])


@MovieInternalRouter.get("/subscribers/{discord_id}")
async def get_subscriber(discord_id: DiscordId, db: Db):
    row = await service.subscriber(db, discord_id)
    return SuccessResponse(data=service.subscription_out(row) if row and row.active else None)


@MovieInternalRouter.put("/subscribers/{discord_id}")
async def put_subscriber(discord_id: DiscordId, data: SubscriptionIn, db: Db):
    row = await service.subscribe(db, discord_id, data)
    job.wake()
    return SuccessResponse(data=service.subscription_out(row))


@MovieInternalRouter.delete("/subscribers/{discord_id}")
async def delete_subscriber(discord_id: DiscordId, db: Db):
    row = await service.subscriber(db, discord_id, lock=True)
    if row:
        row.active = False
        row.snapshot = None
        row.watched_count = None
        row.synced_at = None
        row.revision = str(service.uuid4())
        row.lease_id, row.lease_until = None, None
        row.sync_requested = False
    return SuccessResponse()


@MovieInternalRouter.get("/records/{discord_id}")
async def get_records(discord_id: DiscordId, db: Db):
    return SuccessResponse(data=await service.records(db, discord_id))


@MovieInternalRouter.patch("/records/{discord_id}/{tmdb_id}")
async def update_record(discord_id: DiscordId, tmdb_id: int, data: RecordPatch, db: Db):
    if tmdb_id <= 0 or len(str(data.data)) > 100_000:
        raise HTTPException(422, "推荐记录格式无效")
    return SuccessResponse(data=await service.patch_record(db, discord_id, tmdb_id, data.data, data.revision))


@MovieInternalRouter.post("/cache/{namespace}/read")
async def read_cache(namespace: CacheNamespace, data: CacheKeys, db: Db):
    return SuccessResponse(data=await service.read_cache(db, namespace, data.keys))


@MovieInternalRouter.put("/cache/{namespace}")
async def write_cache(namespace: CacheNamespace, data: CacheBatch, db: Db):
    if any(len(key) > 128 for key in data.items) or len(str(data.items)) > 5_000_000:
        raise HTTPException(422, "快取过大")
    await service.write_cache(db, namespace, data.items)
    return SuccessResponse()


@MovieInternalRouter.post("/ratings")
async def read_ratings(data: RatingsIn, db: Db):
    # OMDb is queried here, not in the bot, so the key and its daily quota live in one place.
    return SuccessResponse(data=await omdb.ratings(db, data.imdbIds))


# Third-party reads for the bot. Each answers {status, body} (Trakt adds pageCount) so the bot sees
# TMDB's / Trakt's own status codes; 503 means admin could not ask (no key, quota, unreachable).
@MovieInternalRouter.post("/sources/tmdb")
async def tmdb_source(data: SourceRequest, db: Db):
    if not tmdb.ALLOWED_PATH.fullmatch(data.path):
        raise HTTPException(422, "不支持的 TMDB 路径")
    try:
        status, body = await tmdb.get(db, data.path, data.params)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    return SuccessResponse(data={"status": status, "body": body})


@MovieInternalRouter.post("/sources/trakt")
async def trakt_source(data: SourceRequest, db: Db):
    if not trakt.ALLOWED_PATH.fullmatch(data.path):
        raise HTTPException(422, "不支持的 Trakt 路径")
    try:
        status, page_count, body = await trakt.get(db, data.path, data.params)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    return SuccessResponse(data={"status": status, "pageCount": page_count, "body": body})


@MovieInternalRouter.post("/sources/wikipedia")
async def wikipedia_source(data: WikimediaRequest, db: Db):
    if not wikipedia.ALLOWED_URL.fullmatch(data.url):
        raise HTTPException(422, "只接受 Wikidata / 英文 Wikipedia 的 api.php")
    try:
        status, body = await wikipedia.get(db, data.url)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    return SuccessResponse(data={"status": status, "body": body})


@MovieInternalRouter.get("/taste/{discord_id}")
async def get_taste(discord_id: DiscordId, db: Db):
    """The taste profile the last sync stored (None before the first one), for the bot's recommendations."""
    row = await service.subscriber(db, discord_id)
    if not row or not row.active or row.taste is None:
        return SuccessResponse(data=None)
    return SuccessResponse(data={"taste": row.taste, "syncedAt": service.subscription_out(row).syncedAt})


@MovieWebsiteRouter.get("/{discord_id}")
async def website_statistics(discord_id: DiscordId, db: Db):
    row = await service.subscriber(db, discord_id)
    if not row or not row.active:
        return SuccessResponse(data={"subscription": None, "snapshot": None, "platform": None})
    return SuccessResponse(data={"subscription": service.subscription_out(row), "snapshot": row.snapshot, "platform": await service.watched_rank(db, row)})


@MovieWebsiteRouter.get("/people/lookup")
async def person_lookup(
    id: Annotated[int | None, Query(ge=1)] = None,
    name: Annotated[str | None, Query(max_length=255)] = None,
    lang: Annotated[Literal["zh", "en"], Query()] = "zh",
):
    """The director card: TMDB person, biography and directed films with public scores."""
    name = (name or "").strip() or None
    if id is None and name is None:
        raise HTTPException(422, "需要 id 或 name")
    try:
        card = await people.lookup(id, name, lang)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if card is None:
        raise HTTPException(404, "找不到这位影人")
    return SuccessResponse(data=card)


@MovieWebsiteRouter.post("/{discord_id}/refresh")
async def website_refresh(discord_id: DiscordId, db: Db):
    await service.request_sync(db, discord_id)
    job.wake()
    return SuccessResponse()


@MovieRouter.get("/list")
async def admin_list(
    db: Db,
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:movies:query"]))],
    page_no: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    total = await db.scalar(select(func.count()).select_from(MovieSubscriberModel))
    rows = await db.scalars(select(MovieSubscriberModel).options(*service.WITHOUT_JSON).order_by(MovieSubscriberModel.subscribed_at.desc()).offset((page_no - 1) * page_size).limit(page_size))
    return SuccessResponse(data={"items": [service.subscription_out(row) for row in rows], "total": total})


@MovieRouter.post("/{discord_id}/refresh")
async def admin_refresh(
    discord_id: DiscordId,
    db: Db,
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:movies:refresh"]))],
):
    await service.request_sync(db, discord_id)
    job.wake()
    return SuccessResponse()


@MovieInternalRouter.post("/imports/owner/{discord_id}")
async def import_inactive_owner(discord_id: DiscordId, db: Db):
    if settings.ENVIRONMENT != "dev":
        raise HTTPException(403, "仅限 dev 导入")
    row = await service.subscriber(db, discord_id, lock=True)
    if row is None:
        row = MovieSubscriberModel(
            discord_id=discord_id,
            trakt_user="legacy-inactive",
            trakt_name="已取消订阅",
            region="TW",
            subscribed_at=service.utcnow(),
            active=False,
            revision=str(service.uuid4()),
            sync_requested=False,
        )
        db.add(row)
    return SuccessResponse()


@MovieInternalRouter.post("/imports/subscribers/{discord_id}")
async def import_subscriber(discord_id: DiscordId, data: SubscriptionIn, db: Db):
    if settings.ENVIRONMENT != "dev":
        raise HTTPException(403, "仅限 dev 导入")
    row = await service.subscriber(db, discord_id, lock=True)
    if row is not None:
        return SuccessResponse(data={"created": False})
    await service.subscribe(db, discord_id, data)
    return SuccessResponse(data={"created": True})
