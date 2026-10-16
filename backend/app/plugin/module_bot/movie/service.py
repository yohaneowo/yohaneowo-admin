from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from .model import MovieCacheModel, MovieRecordModel, MovieSubscriberModel
from .schema import SubscriptionIn, SubscriptionOut

# Loads a subscriber row without its large JSON columns, for queries that sort or list many rows.
WITHOUT_JSON = (defer(MovieSubscriberModel.snapshot), defer(MovieSubscriberModel.taste))


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def subscription_out(row: MovieSubscriberModel) -> SubscriptionOut:
    return SubscriptionOut(
        userId=row.discord_id,
        traktUser=row.trakt_user,
        traktName=row.trakt_name,
        region=row.region,
        subscribedAt=row.subscribed_at,
        active=row.active,
        revision=row.revision,
        syncedAt=row.synced_at,
        syncError=row.sync_error,
        syncRequested=row.sync_requested,
        syncing=bool(row.lease_until and row.lease_until > utcnow()),
    )


async def subscriber(db: AsyncSession, discord_id: str, *, lock: bool = False):
    query = select(MovieSubscriberModel).where(MovieSubscriberModel.discord_id == discord_id)
    if lock:
        query = query.with_for_update()
    return (await db.scalars(query)).first()


async def subscribe(db: AsyncSession, discord_id: str, data: SubscriptionIn):
    row = await subscriber(db, discord_id, lock=True)
    if row is None:
        row = MovieSubscriberModel(discord_id=discord_id)
        db.add(row)
    changed = not row.active or row.trakt_user != data.traktUser
    row.trakt_user, row.trakt_name, row.region = data.traktUser, data.traktName, data.region
    row.active = True
    if changed:
        row.revision = str(uuid4())
        row.snapshot, row.synced_at, row.sync_error = None, None, None
        row.watched_count, row.shows_watched_count, row.anime_watched_count, row.taste = None, None, None, None
        row.lease_id, row.lease_until, row.retry_after = None, None, None
        row.sync_requested = True
        row.subscribed_at = data.subscribedAt.astimezone(UTC).replace(tzinfo=None) if data.subscribedAt else utcnow()
    await db.flush()
    return row


async def require_active(db: AsyncSession, discord_id: str, *, lock: bool = False):
    row = await subscriber(db, discord_id, lock=lock)
    if row is None or not row.active:
        raise HTTPException(404, "没有有效订阅")
    return row


async def request_sync(db: AsyncSession, discord_id: str):
    row = await require_active(db, discord_id, lock=True)
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
            .order_by(model.synced_at, model.discord_id)
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


async def finish_sync(db: AsyncSession, discord_id: str, revision: str, lease_id: str, snapshot: dict | None, taste: dict | None, *, again: bool = False) -> bool:
    """Store a sync's result (snapshot None = failed). False when the lease is stale: the
    subscription changed or another sync took over, so the result is dropped. again: sync once
    more right away (the anime lookups ran out of time)."""
    row = await subscriber(db, discord_id, lock=True)
    if row is None or not row.active or row.revision != revision or row.lease_id != lease_id:
        return False
    row.lease_id, row.lease_until = None, None
    if snapshot is None:
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


async def records(db: AsyncSession, discord_id: str):
    rows = await db.scalars(select(MovieRecordModel).where(MovieRecordModel.discord_id == discord_id))
    return {str(row.tmdb_id): row.data for row in rows}


async def patch_record(db: AsyncSession, discord_id: str, tmdb_id: int, data: dict, revision: str | None):
    # Lock the parent so concurrent feedback and recommendation updates cannot lose fields.
    parent = await subscriber(db, discord_id, lock=True)
    if parent is None:
        raise HTTPException(404, "订阅记录不存在")
    if revision is not None and (not parent.active or parent.revision != revision):
        raise HTTPException(409, "订阅已变更")
    row = await db.get(MovieRecordModel, (discord_id, tmdb_id))
    if row is None:
        row = MovieRecordModel(discord_id=discord_id, tmdb_id=tmdb_id, data={})
        db.add(row)
    row.data = {**row.data, **data}
    await db.flush()
    return row.data
