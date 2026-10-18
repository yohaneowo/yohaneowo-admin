from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter

from ..internal_auth import verify_bot_token
from . import anime_recommend, credits, people, recommend, service
from . import bonds as bonds_module
from . import bonus as bonus_picks
from .auth import verify_site_token
from .model import MovieSubscriberModel
from .schema import (
    AnimePickIn,
    AnimeSettingsIn,
    BondsIn,
    BonusIn,
    CacheBatch,
    CacheKeys,
    DiscordId,
    PickIn,
    RatingsIn,
    RecordPatch,
    RegionIn,
    SiteUserId,
    SourceRequest,
    SubscriptionIn,
    TraktConnectIn,
    WatchedIn,
    WikimediaRequest,
)
from .sources import omdb, tmdb, trakt, wikipedia
from .sources.base import SourceUnavailable
from .sync import job
from .sync.history import TraktTokenError

Db = Annotated[AsyncSession, Depends(db_getter, scope="function")]
CacheNamespace = Literal["tmdb", "embeddings", "wikipedia", "tmdb_person", "tmdb_collection"]
# job.ensure_loop starts the statistics sync loop in this process the first time any movie route is used.
MovieInternalRouter = APIRouter(prefix="/internal/movies", tags=["电影内部接口"], dependencies=[Depends(verify_bot_token), Depends(job.ensure_loop)])
MovieWebsiteRouter = APIRouter(prefix="/website/movies", tags=["网站观影统计"], dependencies=[Depends(verify_site_token), Depends(job.ensure_loop)])
MovieRouter = APIRouter(prefix="/movies", tags=["电影订阅管理"], dependencies=[Depends(job.ensure_loop)])


# ---------------------------------------------------------------------------------------------
# The bot: everything is addressed by the Discord user; a profile must already exist (people
# enter their Trakt details on the website, which links the profile to their Discord account).


async def _bot_profile(db: AsyncSession, discord_id: str, *, lock: bool = False):
    row = await service.by_discord(db, discord_id, lock=lock)
    if row is None or not row.active:
        raise HTTPException(404, "这个 Discord 帐号还没有观影档案")
    return row


@MovieInternalRouter.get("/subscribers")
async def list_subscribers(db: Db):
    return SuccessResponse(data=[service.subscription_out(row) for row in await service.recommendation_targets(db)])


@MovieInternalRouter.get("/subscribers/{discord_id}")
async def get_subscriber(discord_id: DiscordId, db: Db):
    return SuccessResponse(data=service.bot_view(await service.by_discord(db, discord_id)))


@MovieInternalRouter.get("/profiles/{discord_id}")
async def get_profile_link(discord_id: DiscordId, db: Db):
    """For the bot's /movie profile: whose website page this Discord user's profile is. Unlike
    /subscribers it doesn't care whether daily recommendations are on; null without an active
    profile."""
    row = await service.by_discord(db, discord_id)
    if row is None or not row.active:
        return SuccessResponse(data=None)
    return SuccessResponse(data={"siteUserId": row.site_user_id, "traktUser": row.trakt_user, "traktName": row.trakt_name, "hasSnapshot": bool(row.snapshot)})


@MovieInternalRouter.put("/subscribers/{discord_id}")
async def put_subscriber(discord_id: DiscordId, db: Db):
    """Turn daily recommendations on. 404 when the Discord user has no profile yet."""
    row = await service.set_recommendations(db, discord_id, True)
    if row is None:
        raise HTTPException(404, "这个 Discord 帐号还没有观影档案")
    job.wake()
    return SuccessResponse(data=service.subscription_out(row))


@MovieInternalRouter.delete("/subscribers/{discord_id}")
async def delete_subscriber(discord_id: DiscordId, db: Db):
    """Turn daily recommendations off; the profile and its statistics stay."""
    await service.set_recommendations(db, discord_id, False)
    return SuccessResponse()


# /movie recommend: random picks from the profile's pools. The Trakt token is required (403
# traktRequired), three picks a day (429 dailyLimit), an empty pool answers 404 nothingToPick.
def _pick_errors(error: Exception) -> HTTPException:
    if isinstance(error, recommend.TraktRequired):
        return HTTPException(403, "traktRequired")
    if isinstance(error, recommend.DailyLimit):
        return HTTPException(429, "dailyLimit")
    if isinstance(error, recommend.NothingToPick):
        return HTTPException(404, "nothingToPick")
    if isinstance(error, LookupError):
        return HTTPException(404, "pickNotFound")
    if isinstance(error, SourceUnavailable):
        return HTTPException(503, str(error))
    return HTTPException(502, "traktTokenRejected")


@MovieInternalRouter.post("/picks/{discord_id}")
async def post_pick(discord_id: DiscordId, data: PickIn, db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        card = await recommend.pick(db, row, {"genre": data.genre, "decade": data.decade, "maxRuntime": data.maxRuntime}, data.skip)
    except (recommend.TraktRequired, recommend.DailyLimit, recommend.NothingToPick, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error
    return SuccessResponse(data=card)


@MovieInternalRouter.get("/picks/{discord_id}")
async def list_picks(discord_id: DiscordId, db: Db):
    """Today's quota and the recent picks, newest first."""
    row = await _bot_profile(db, discord_id)
    recent = await recommend._recent_picks(db, row.profile_id)  # noqa: SLF001
    return SuccessResponse(
        data={
            "remainingToday": await recommend._remaining(db, row),  # noqa: SLF001
            "quota": recommend.quota_for(row),
            "bonus": await bonus_picks.left(db, row.profile_id, "movie"),
            "traktConnected": service.tokens_of(row) is not None and not row.trakt_reauth,
            "picks": [recommend.pick_out(p) for p in sorted(recent, key=lambda p: p.shown_at or service.utcnow(), reverse=True)],
        }
    )


@MovieInternalRouter.get("/picks/{discord_id}/progress")
async def get_pick_progress(discord_id: DiscordId, db: Db):
    """Which stage a running pick is at (lists / profile / pools / card), or null when idle."""
    row = await _bot_profile(db, discord_id)
    return SuccessResponse(data=await recommend.progress_of(row.profile_id))


@MovieInternalRouter.post("/picks/{discord_id}/{pick_id}/watchlist")
async def post_pick_watchlist(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await recommend.want(db, row, pick_id))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


@MovieInternalRouter.post("/picks/{discord_id}/{pick_id}/watched")
async def post_pick_watched(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], data: WatchedIn, db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await recommend.watched(db, row, pick_id, data.rating))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


@MovieInternalRouter.post("/picks/{discord_id}/{pick_id}/unrate")
async def post_pick_unrate(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], db: Db):
    """删除评分: remove a rating (and the play) this pick wrote to Trakt; the refund goes back."""
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await recommend.unrate(db, row, pick_id))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


# /anime recommend: the same contract as /movie recommend (403 traktRequired, 429 dailyLimit,
# 404 nothingToPick / pickNotFound), its own quota.
@MovieInternalRouter.post("/anime-picks/{discord_id}")
async def post_anime_pick(discord_id: DiscordId, data: AnimePickIn, db: Db):
    row = await _bot_profile(db, discord_id)
    filters = {"genre": data.genre, "decade": data.decade, "format": data.format, "length": data.length, "airing": data.airing}
    try:
        card = await anime_recommend.pick(db, row, filters, data.skip)
    except (recommend.TraktRequired, recommend.DailyLimit, recommend.NothingToPick, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error
    return SuccessResponse(data=card)


@MovieInternalRouter.get("/anime-picks/{discord_id}")
async def list_anime_picks(discord_id: DiscordId, db: Db):
    """Today's anime quota and the recent anime picks, newest first."""
    row = await _bot_profile(db, discord_id)
    recent = await anime_recommend._recent_picks(db, row.profile_id)  # noqa: SLF001
    return SuccessResponse(
        data={
            "remainingToday": await anime_recommend.quota_left(db, row),
            "quota": anime_recommend.quota_for(row),
            "bonus": await bonus_picks.left(db, row.profile_id, "anime"),
            "airing": bool(row.anime_airing),
            "traktConnected": service.tokens_of(row) is not None and not row.trakt_reauth,
            "picks": [anime_recommend.pick_out(p) for p in sorted(recent, key=lambda p: p.shown_at or service.utcnow(), reverse=True)],
        }
    )


async def _owner_features(db: AsyncSession, row: MovieSubscriberModel) -> list[dict]:
    """Per feature with a bonus: the day's picks left, the daily quota and the bonus left."""
    daily = {"anime": (anime_recommend.quota_left, anime_recommend.quota_for), "movie": (recommend._remaining, recommend.quota_for)}  # noqa: SLF001
    return [
        {
            "feature": feature,
            "remainingToday": await daily[feature][0](db, row),
            "quota": daily[feature][1](row),
            "bonus": await bonus_picks.left(db, row.profile_id, feature),
        }
        for feature in bonus_picks.FEATURES
    ]


@MovieInternalRouter.get("/owner/{discord_id}")
async def get_owner_view(discord_id: DiscordId, db: Db):
    """/owner check (the bot checks it is the owner asking): someone's quotas and bonus picks."""
    row = await _bot_profile(db, discord_id)
    return SuccessResponse(data={"traktName": row.trakt_name, "traktUser": row.trakt_user, "features": await _owner_features(db, row)})


@MovieInternalRouter.post("/owner/{discord_id}/bonus")
async def post_owner_bonus(discord_id: DiscordId, data: BonusIn, db: Db):
    """/owner bonus (the bot checks it is the owner asking): add bonus picks to someone's feature."""
    row = await _bot_profile(db, discord_id)
    bonus = await bonus_picks.add(db, row.profile_id, data.feature, data.amount)
    return SuccessResponse(data={"feature": data.feature, "bonus": bonus, "features": await _owner_features(db, row)})


@MovieInternalRouter.get("/anime-picks/{discord_id}/progress")
async def get_anime_pick_progress(discord_id: DiscordId, db: Db):
    row = await _bot_profile(db, discord_id)
    return SuccessResponse(data=await anime_recommend.progress_of(row.profile_id))


@MovieInternalRouter.post("/anime-picks/{discord_id}/{pick_id}/watchlist")
async def post_anime_pick_watchlist(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await anime_recommend.want(db, row, pick_id))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


@MovieInternalRouter.post("/anime-picks/{discord_id}/{pick_id}/watched")
async def post_anime_pick_watched(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], data: WatchedIn, db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await anime_recommend.watched(db, row, pick_id, data.rating))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


@MovieInternalRouter.post("/anime-picks/{discord_id}/{pick_id}/unrate")
async def post_anime_pick_unrate(discord_id: DiscordId, pick_id: Annotated[str, Field(max_length=36)], db: Db):
    row = await _bot_profile(db, discord_id)
    try:
        return SuccessResponse(data=await anime_recommend.unrate(db, row, pick_id))
    except (recommend.TraktRequired, LookupError, SourceUnavailable, TraktTokenError) as error:
        raise _pick_errors(error) from error


@MovieInternalRouter.get("/records/{discord_id}")
async def get_records(discord_id: DiscordId, db: Db):
    row = await _bot_profile(db, discord_id)
    return SuccessResponse(data=await service.records(db, row.profile_id))


@MovieInternalRouter.patch("/records/{discord_id}/{tmdb_id}")
async def update_record(discord_id: DiscordId, tmdb_id: int, data: RecordPatch, db: Db):
    if tmdb_id <= 0 or len(str(data.data)) > 100_000:
        raise HTTPException(422, "推荐记录格式无效")
    row = await _bot_profile(db, discord_id, lock=True)
    return SuccessResponse(data=await service.patch_record(db, row, tmdb_id, data.data, data.revision))


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
    row = await service.by_discord(db, discord_id)
    if not row or not row.active or row.taste is None:
        return SuccessResponse(data=None)
    return SuccessResponse(data={"taste": row.taste, "syncedAt": service.subscription_out(row).syncedAt})


# ---------------------------------------------------------------------------------------------
# The website: addressed by the website account, with the account's linked Discord user (if any)
# so a profile the bot created earlier is picked up, and the bot can find one created here.


def _website_view(row: MovieSubscriberModel | None, platform: dict | None) -> dict:
    if row is None or not row.active:
        return {"subscription": None, "snapshot": None, "platform": None}
    return {"subscription": service.subscription_out(row), "snapshot": row.snapshot, "platform": platform}


@MovieWebsiteRouter.get("/users/{site_user_id}")
async def website_statistics(site_user_id: SiteUserId, db: Db, discordId: DiscordId | None = None):
    row = await service.site_profile(db, site_user_id, discordId)
    platform = await service.watched_rank(db, row) if row and row.active else None
    return SuccessResponse(data=_website_view(row, platform))


@MovieWebsiteRouter.get("/users/{site_user_id}/view")
async def website_view_statistics(site_user_id: SiteUserId, db: Db, discordId: DiscordId | None = None):
    """Someone else's statistics page (or the viewer's own data for the comparison radar):
    the same view as above without the claim and merge that only the owner's visit may do."""
    row = await service.peek_profile(db, site_user_id, discordId)
    platform = await service.watched_rank(db, row) if row and row.active else None
    return SuccessResponse(data=_website_view(row, platform))


@MovieWebsiteRouter.get("/trakt/authorize")
async def trakt_authorize(
    redirectUri: Annotated[str, Query(max_length=500, pattern=r"^https?://")],
    # The website's signed state (base64 JSON + HMAC) runs well past 200 characters.
    state: Annotated[str, Query(max_length=1000)],
    codeChallenge: Annotated[str, Query(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
):
    """Where to send the browser to authorize this app on Trakt (the client id lives here); the
    website supplies the PKCE challenge and keeps the verifier."""
    try:
        return SuccessResponse(data={"url": trakt.authorize_url(redirectUri, state, codeChallenge)})
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error


@MovieWebsiteRouter.post("/users/{site_user_id}/trakt")
async def website_connect_trakt(site_user_id: SiteUserId, data: TraktConnectIn, db: Db):
    """Trakt sent the browser back with a code: exchange it, read who authorized, and make that
    Trakt account the website account's profile. 409 traktTaken when another website account
    already has it; 422 traktDenied when the code is no good."""
    try:
        tokens = await trakt.exchange_code(db, data.code, data.redirectUri, data.codeVerifier)
        me = await trakt.me(db, tokens.access_token)
    except trakt.TraktAuthError as error:
        raise HTTPException(422, "traktDenied") from error
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    try:
        row = await service.connect_trakt(db, site_user_id, data.discordId, data.region, tokens, data.redirectUri, me)
    except service.TraktTaken as error:
        raise HTTPException(409, "traktTaken") from error
    job.wake()
    return SuccessResponse(data=_website_view(row, None))


@MovieWebsiteRouter.post("/users/{site_user_id}/bonds")
async def website_bonds(site_user_id: SiteUserId, data: BondsIn, db: Db):
    """羁绊: what the account has watched in common with each of these Discord users' profiles."""
    me = await service.by_site_user(db, site_user_id)
    if me is None or not me.active:
        raise HTTPException(404, "noProfile")
    others = []
    for discord_id in dict.fromkeys(data.discordIds):
        other = await service.by_discord(db, discord_id)
        if other is not None and other.active:
            others.append(other)
    return SuccessResponse(data={"bonds": bonds_module.bonds(me, others)})


@MovieWebsiteRouter.get("/users/{site_user_id}/anime-settings")
async def website_anime_settings(site_user_id: SiteUserId, db: Db):
    """The account settings page: whether /anime recommend includes airing anime."""
    row = await service.by_site_user(db, site_user_id)
    if row is None or not row.active:
        raise HTTPException(404, "noProfile")
    return SuccessResponse(data={"airing": bool(row.anime_airing)})


@MovieWebsiteRouter.put("/users/{site_user_id}/anime-settings")
async def website_set_anime_settings(site_user_id: SiteUserId, data: AnimeSettingsIn, db: Db):
    row = await service.by_site_user(db, site_user_id, lock=True)
    if row is None or not row.active:
        raise HTTPException(404, "noProfile")
    row.anime_airing = data.airing
    return SuccessResponse(data={"airing": bool(row.anime_airing)})


@MovieWebsiteRouter.put("/users/{site_user_id}/region")
async def website_set_region(site_user_id: SiteUserId, data: RegionIn, db: Db):
    return SuccessResponse(data=_website_view(await service.set_region(db, site_user_id, data.region), None))


@MovieWebsiteRouter.post("/users/{site_user_id}/refresh")
async def website_refresh(site_user_id: SiteUserId, db: Db):
    await service.request_sync(db, await service.by_site_user(db, site_user_id, lock=True))
    job.wake()
    return SuccessResponse()


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


CreditKind = Annotated[Literal["studio", "director", "original", "music", "chiefAnimationDirector", "cast"], Query()]
PositiveId = Annotated[int | None, Query(ge=1)]


async def _credit_who(kind: str, anilist_id: int | None, bangumi_id: int | None, tmdb_id: int | None, name: str | None) -> credits.Who:
    """Whose card: voice actors arrive as TMDB people and are looked up on AniList and Bangumi."""
    if kind == "cast":
        if tmdb_id is None:
            raise HTTPException(422, "声优需要 tmdbId")
        anilist_id, bangumi_id = await credits.cast_identity(tmdb_id, (name or "").strip() or None)
        if anilist_id is None and bangumi_id is None:
            raise HTTPException(404, "找不到这位声优")
    elif anilist_id is None and bangumi_id is None:
        raise HTTPException(422, "需要 anilistId 或 bangumiId")
    return credits.Who(kind, anilist_id, bangumi_id)


@MovieWebsiteRouter.get("/credits/info")
async def credit_info(
    kind: CreditKind,
    anilistId: PositiveId = None,
    bangumiId: PositiveId = None,
    tmdbId: PositiveId = None,
    name: Annotated[str | None, Query(max_length=255)] = None,
    lang: Annotated[Literal["zh", "en"], Query()] = "zh",
):
    """An anime credit card's head (动画制作, 监督, 原作, 音乐, 总作画监督, 声优): name, picture
    and description."""
    try:
        who = await _credit_who(kind, anilistId, bangumiId, tmdbId, name)
        card = await credits.info(who, lang)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if card is None:
        raise HTTPException(404, "找不到这个人")
    return SuccessResponse(data=card)


@MovieWebsiteRouter.get("/credits/works")
async def credit_works(
    kind: CreditKind,
    anilistId: PositiveId = None,
    bangumiId: PositiveId = None,
    tmdbId: PositiveId = None,
    name: Annotated[str | None, Query(max_length=255)] = None,
    lang: Annotated[Literal["zh", "en"], Query()] = "zh",
):
    """An anime credit card's works as known right now; the site polls while `building` or
    `scoresPending` is set (the rest is fetched in the background)."""
    try:
        who = await _credit_who(kind, anilistId, bangumiId, tmdbId, name)
    except SourceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    card = await credits.works(who, lang)
    if card is None:
        raise HTTPException(404, "找不到作品")
    return SuccessResponse(data=card)


# ---------------------------------------------------------------------------------------------
# The admin UI.


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


@MovieRouter.post("/{profile_id}/refresh")
async def admin_refresh(
    profile_id: Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{36}$")],
    db: Db,
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:movies:refresh"]))],
):
    await service.request_sync(db, await service.by_id(db, profile_id, lock=True))
    job.wake()
    return SuccessResponse()


# ---------------------------------------------------------------------------------------------
# Dev-only imports of the bot's old JSON files (scripts/import-movies.js in the bot repo).


@MovieInternalRouter.post("/imports/owner/{discord_id}")
async def import_inactive_owner(discord_id: DiscordId, db: Db):
    if settings.ENVIRONMENT != "dev":
        raise HTTPException(403, "仅限 dev 导入")
    row = await service.by_discord(db, discord_id, lock=True)
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
    row = await service.by_discord(db, discord_id, lock=True)
    if row is not None:
        return SuccessResponse(data={"created": False})
    row = await service.subscribe(db, discord_id, data)
    row.recommendations = True
    return SuccessResponse(data={"created": True})
