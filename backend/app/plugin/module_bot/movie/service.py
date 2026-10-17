from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.utils.crypto_util import CryptoUtil

from .model import MovieCacheModel, MovieRecordModel, MovieSubscriberModel
from .schema import SubscriptionIn, SubscriptionOut
from .sources.trakt import TraktTokens

# Loads a profile row without its large JSON columns, for queries that sort or list many rows.
WITHOUT_JSON = (defer(MovieSubscriberModel.snapshot), defer(MovieSubscriberModel.taste))


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def subscription_out(row: MovieSubscriberModel) -> SubscriptionOut:
    return SubscriptionOut(
        profileId=row.profile_id,
        userId=row.discord_id,
        siteUserId=row.site_user_id,
        traktUser=row.trakt_user,
        traktName=row.trakt_name,
        traktAvatar=row.trakt_avatar,
        traktPrivate=row.trakt_private,
        traktConnected=bool(row.trakt_refresh_token),
        traktReauth=row.trakt_reauth,
        region=row.region,
        subscribedAt=row.subscribed_at,
        active=row.active,
        recommendations=row.recommendations,
        revision=row.revision,
        syncedAt=row.synced_at,
        syncError=row.sync_error,
        syncRequested=row.sync_requested,
        syncing=bool(row.lease_until and row.lease_until > utcnow()),
    )


def bot_view(row: MovieSubscriberModel | None) -> SubscriptionOut | None:
    """What the bot sees as "subscribed": an active profile with recommendations on."""
    return subscription_out(row) if row and row.active and row.recommendations else None


async def _one(db: AsyncSession, query, *, lock: bool):
    if lock:
        query = query.with_for_update()
    return (await db.scalars(query)).first()


async def by_id(db: AsyncSession, profile_id: str, *, lock: bool = False):
    return await _one(db, select(MovieSubscriberModel).where(MovieSubscriberModel.profile_id == profile_id), lock=lock)


async def by_discord(db: AsyncSession, discord_id: str, *, lock: bool = False):
    return await _one(db, select(MovieSubscriberModel).where(MovieSubscriberModel.discord_id == discord_id), lock=lock)


async def by_site_user(db: AsyncSession, site_user_id: str, *, lock: bool = False):
    return await _one(db, select(MovieSubscriberModel).where(MovieSubscriberModel.site_user_id == site_user_id), lock=lock)


def set_trakt(row: MovieSubscriberModel, trakt_user: str, trakt_name: str, region: str, subscribed_at: datetime | None = None) -> None:
    """Point a profile at a Trakt account. A different account (or a reactivation) starts the
    statistics over; only changing the region keeps them."""
    changed = not row.active or row.trakt_user != trakt_user
    row.trakt_user, row.trakt_name, row.region = trakt_user, trakt_name, region
    row.active = True
    if changed:
        row.revision = str(uuid4())
        row.snapshot, row.synced_at, row.sync_error = None, None, None
        row.watched_count, row.shows_watched_count, row.anime_watched_count, row.taste = None, None, None, None
        row.lease_id, row.lease_until, row.retry_after = None, None, None
        row.sync_requested = True
        row.subscribed_at = subscribed_at.astimezone(UTC).replace(tzinfo=None) if subscribed_at else utcnow()


async def subscribe(db: AsyncSession, discord_id: str, data: SubscriptionIn):
    """Create or update the profile owned by a Discord user (the bot's dev import)."""
    row = await by_discord(db, discord_id, lock=True)
    if row is None:
        row = MovieSubscriberModel(discord_id=discord_id, revision=str(uuid4()))
        db.add(row)
    set_trakt(row, data.traktUser, data.traktName, data.region, data.subscribedAt)
    await db.flush()
    return row


async def merge(db: AsyncSession, keep: MovieSubscriberModel, drop: MovieSubscriberModel) -> None:
    """Fold one profile into another (the website account's profile wins): the Discord link,
    recommendation setting and recommendation history move over, then `drop` is deleted."""
    kept_ids = {row.tmdb_id for row in await db.scalars(select(MovieRecordModel).where(MovieRecordModel.profile_id == keep.profile_id))}
    for record in await db.scalars(select(MovieRecordModel).where(MovieRecordModel.profile_id == drop.profile_id)):
        if record.tmdb_id in kept_ids:
            await db.delete(record)
        else:
            record.profile_id = keep.profile_id
    discord_id = drop.discord_id
    keep.recommendations = keep.recommendations or drop.recommendations
    await db.delete(drop)
    await db.flush()
    if discord_id and not keep.discord_id:
        keep.discord_id = discord_id
    await db.flush()


async def site_profile(db: AsyncSession, site_user_id: str, discord_id: str | None, *, lock: bool = False):
    """The profile behind a website account. A Discord user who linked their account on the
    website brings the profile the bot created for them; if both sides have one, the website's
    is kept and the other merged into it."""
    row = await by_site_user(db, site_user_id, lock=lock)
    other = await by_discord(db, discord_id, lock=lock) if discord_id else None
    if row and other and other.profile_id != row.profile_id:
        await merge(db, keep=row, drop=other)
        return row
    if row:
        if discord_id and not row.discord_id:
            row.discord_id = discord_id
            await db.flush()
        return row
    # The bot's profile is claimed only while no other website account owns it.
    if other and other.site_user_id is None:
        other.site_user_id = site_user_id
        await db.flush()
        return other
    return None


class TraktTaken(Exception):
    """The Trakt account already belongs to another website account's profile."""


async def by_trakt(db: AsyncSession, trakt_user: str, *, lock: bool = False):
    return await _one(db, select(MovieSubscriberModel).where(MovieSubscriberModel.trakt_user == trakt_user), lock=lock)


def store_tokens(row: MovieSubscriberModel, tokens: TraktTokens, redirect_uri: str | None) -> None:
    row.trakt_access_token = CryptoUtil.encrypt(tokens.access_token)
    row.trakt_refresh_token = CryptoUtil.encrypt(tokens.refresh_token)
    row.trakt_token_expires_at = tokens.expires_at
    row.trakt_redirect_uri = redirect_uri
    row.trakt_reauth = False


def tokens_of(row: MovieSubscriberModel) -> TraktTokens | None:
    if not row.trakt_refresh_token or not row.trakt_access_token:
        return None
    return TraktTokens(
        access_token=CryptoUtil.decrypt(row.trakt_access_token),
        refresh_token=CryptoUtil.decrypt(row.trakt_refresh_token),
        expires_at=row.trakt_token_expires_at or utcnow(),
    )


async def connect_trakt(db: AsyncSession, site_user_id: str, discord_id: str | None, region: str, tokens: TraktTokens, redirect_uri: str, me: dict):
    """The website account authorized a Trakt account: make it the account's profile. A profile
    the bot created for the same Trakt account (not yet owned by a website account) is claimed;
    one owned by another website account is a conflict (TraktTaken)."""
    slug = str((me.get("ids") or {}).get("slug") or me.get("username") or "")
    if not slug:
        raise ValueError("Trakt profile without a slug")
    row = await site_profile(db, site_user_id, discord_id, lock=True)
    holder = await by_trakt(db, slug, lock=True)
    if holder is not None and (row is None or holder.profile_id != row.profile_id):
        if holder.site_user_id is not None:
            raise TraktTaken()
        if row is None:
            holder.site_user_id = site_user_id
            row = holder
        else:
            await merge(db, keep=row, drop=holder)
    if row is None:
        row = MovieSubscriberModel(site_user_id=site_user_id, discord_id=discord_id, revision=str(uuid4()))
        db.add(row)
    set_trakt(row, slug, str(me.get("username") or slug), region)
    row.trakt_avatar = ((me.get("images") or {}).get("avatar") or {}).get("full") or None
    row.trakt_private = bool(me.get("private"))
    store_tokens(row, tokens, redirect_uri)
    await db.flush()
    return row


async def set_region(db: AsyncSession, site_user_id: str, region: str):
    row = await by_site_user(db, site_user_id, lock=True)
    if row is None or not row.active:
        raise HTTPException(404, "没有有效订阅")
    row.region = region
    await db.flush()
    return row


async def set_recommendations(db: AsyncSession, discord_id: str, enabled: bool):
    """The bot's subscribe / unsubscribe: only flips the daily DMs; the profile itself stays."""
    row = await by_discord(db, discord_id, lock=True)
    if row is None or not row.active:
        return None
    row.recommendations = enabled
    await db.flush()
    return row


async def recommendation_targets(db: AsyncSession):
    """Profiles the bot sends daily recommendations to."""
    model = MovieSubscriberModel
    query = select(model).options(*WITHOUT_JSON).where(model.active.is_(True), model.recommendations.is_(True), model.discord_id.is_not(None))
    return list(await db.scalars(query))


async def request_sync(db: AsyncSession, row: MovieSubscriberModel | None):
    if row is None or not row.active:
        raise HTTPException(404, "没有有效订阅")
    # Repeated clicks never bypass worker failure backoff or a running lease.
    if row.synced_at and row.synced_at > utcnow() - timedelta(minutes=5):
        raise HTTPException(429, "请稍后再更新")
    row.sync_requested = True


async def claim(db: AsyncSession):
    model = MovieSubscriberModel
    now = utcnow()
    row = (
        await db.scalars(
            # The JSON columns are large; MySQL sorts whole selected rows and runs out of sort memory.
            select(model)
            .options(*WITHOUT_JSON)
            .where(
                model.active.is_(True),
                or_(model.lease_until.is_(None), model.lease_until < now),
                or_(model.retry_after.is_(None), model.retry_after <= now),
                or_(model.sync_requested.is_(True), model.synced_at.is_(None), model.synced_at < now - timedelta(hours=24)),
            )
            .order_by(model.synced_at, model.profile_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    row.lease_id = str(uuid4())
    row.lease_until = now + timedelta(minutes=15)
    return {**subscription_out(row).model_dump(mode="json"), "leaseId": row.lease_id}


async def _rank(db: AsyncSession, column, value: int | None) -> tuple[int | None, int]:
    """(share of active users with a count at or below value, 0–100; how many users have a count)."""
    if value is None:
        return None, 0
    counted = select(func.count()).select_from(MovieSubscriberModel).where(MovieSubscriberModel.active.is_(True), column.is_not(None))
    users = await db.scalar(counted) or 0
    at_or_below = await db.scalar(counted.where(column <= value)) or 0
    return (round(at_or_below / users * 100) if users else None), users


async def watched_rank(db: AsyncSession, row: MovieSubscriberModel) -> dict | None:
    """Where this user stands among active users by films watched, and by shows watched."""
    if row.watched_count is None:
        return None
    films, users = await _rank(db, MovieSubscriberModel.watched_count, row.watched_count)
    shows, show_users = await _rank(db, MovieSubscriberModel.shows_watched_count, row.shows_watched_count)
    anime, anime_users = await _rank(db, MovieSubscriberModel.anime_watched_count, row.anime_watched_count)
    return {
        "watchedPercentile": films,
        "users": users,
        "showsPercentile": shows,
        "showUsers": show_users,
        "animePercentile": anime,
        "animeUsers": anime_users,
    }


SYNC_ERROR = "暂时无法从 Trakt / TMDB 更新，稍后自动重试。"
REAUTH_ERROR = "Trakt 授权已失效，请在网站重新授权。"


async def finish_sync(db: AsyncSession, profile_id: str, revision: str, lease_id: str, snapshot: dict | None, taste: dict | None, *, again: bool = False, reauth: bool = False) -> bool:
    """Store a sync's result (snapshot None = failed). False when the lease is stale: the
    profile changed or another sync took over, so the result is dropped. again: sync once
    more right away (the anime lookups ran out of time). reauth: Trakt rejected the owner's
    token, so syncs pause until they authorize again."""
    row = await by_id(db, profile_id, lock=True)
    if row is None or not row.active or row.revision != revision or row.lease_id != lease_id:
        return False
    row.lease_id, row.lease_until = None, None
    if reauth:
        row.trakt_reauth = True
        row.sync_error = REAUTH_ERROR
        # Not before a new authorization (connect_trakt clears the flag and requests a sync).
        row.sync_requested = False
        row.retry_after = utcnow() + timedelta(days=3650)
    elif snapshot is None:
        row.sync_error = SYNC_ERROR
        row.retry_after = utcnow() + timedelta(minutes=5)
    else:
        row.snapshot, row.taste = snapshot, taste
        row.watched_count = snapshot.get("watchedCount")
        row.shows_watched_count = (snapshot.get("shows") or {}).get("watchedCount")
        row.anime_watched_count = (snapshot.get("anime") or {}).get("watchedCount")
        row.synced_at = utcnow()
        row.sync_error, row.retry_after = None, None
        row.sync_requested = again
    await db.flush()
    return True


async def read_cache(db: AsyncSession, namespace: str, keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    rows = await db.scalars(select(MovieCacheModel).where(MovieCacheModel.namespace == namespace, MovieCacheModel.cache_key.in_(keys)))
    return {row.cache_key: row.data for row in rows}


async def write_cache(db: AsyncSession, namespace: str, items: dict[str, dict]) -> None:
    values = [{"namespace": namespace, "cache_key": key, "data": value} for key, value in sorted(items.items())]
    if values and db.bind.dialect.name == "mysql":
        statement = mysql_insert(MovieCacheModel).values(values)
        await db.execute(statement.on_duplicate_key_update(data=statement.inserted.data))
    else:
        for value in values:
            row = await db.get(MovieCacheModel, (namespace, value["cache_key"]))
            if row is None:
                db.add(MovieCacheModel(**value))
            else:
                row.data = value["data"]
    await db.flush()


async def records(db: AsyncSession, profile_id: str):
    rows = await db.scalars(select(MovieRecordModel).where(MovieRecordModel.profile_id == profile_id))
    return {str(row.tmdb_id): row.data for row in rows}


async def patch_record(db: AsyncSession, parent: MovieSubscriberModel, tmdb_id: int, data: dict, revision: str | None):
    """`parent` must be locked by the caller so concurrent feedback and recommendation updates cannot lose fields."""
    if revision is not None and (not parent.active or parent.revision != revision):
        raise HTTPException(409, "订阅已变更")
    row = await db.get(MovieRecordModel, (parent.profile_id, tmdb_id))
    if row is None:
        row = MovieRecordModel(profile_id=parent.profile_id, tmdb_id=tmdb_id, data={})
        db.add(row)
    row.data = {**row.data, **data}
    await db.flush()
    return row.data
