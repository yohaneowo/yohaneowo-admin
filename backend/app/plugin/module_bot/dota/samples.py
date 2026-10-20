"""Rank samples for DotaHub's comparisons: a hero's ranked matches at a rank tier (medal 1–8), so a
player can be compared with that tier's players of the hero. Public matches are mostly Turbo and
unranked, so they aren't searched for the hero: OpenDota's public matches of the tier give players
(STRATZ tells each one's medal), and STRATZ gives each of those players' own ranked matches on the
hero, several per player (which also gives each player's volatility). Collected in the background
while the website asks for the sample, refreshed with newer matches every few days."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from . import opendota, stratz
from .model import DotaSampleModel, DotaSampleWantModel

# Enough matches for spreads and box plots; the newest KEEP are kept when refreshing.
TARGET = 100
KEEP = 200
CHECK = timedelta(minutes=1)
REFRESH = timedelta(days=3)
RETRY = timedelta(minutes=10)
# Nobody asked for this long: stop collecting.
FORGET = timedelta(days=14)
# Players' matches this recent (the current patch, more or less).
WINDOW = timedelta(days=90)
# OpenDota's newest public matches aren't on STRATZ yet: start this far back. Match ids grow about
# ID_RATE a second.
SETTLE = timedelta(hours=3)
ID_RATE = 11
# Players read per check (one STRATZ request) and remembered (so nobody is read twice).
SEEN = 2000


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def usable_count(db: AsyncSession, hero_id: int, tier: int) -> int:
    return int(await db.scalar(select(func.count()).select_from(DotaSampleModel).where(DotaSampleModel.hero_id == hero_id, DotaSampleModel.tier == tier, DotaSampleModel.data.is_not(None))) or 0)


async def want(db: AsyncSession, hero_id: int, tier: int) -> DotaSampleWantModel:
    """The website asked for the sample: keep collecting it (starting now when it's new)."""
    now = utcnow()
    row = await db.get(DotaSampleWantModel, (hero_id, tier), with_for_update=True)
    if row is None:
        row = DotaSampleWantModel(hero_id=hero_id, tier=tier, requested_at=now, next_check_at=now)
        db.add(row)
    else:
        row.requested_at = now
        if row.next_check_at is None:
            row.next_check_at = now
    await db.flush()
    return row


async def stored(db: AsyncSession, hero_id: int, tier: int, limit: int) -> list[DotaSampleModel]:
    """The newest usable matches of the sample."""
    query = select(DotaSampleModel).where(DotaSampleModel.hero_id == hero_id, DotaSampleModel.tier == tier, DotaSampleModel.data.is_not(None)).order_by(DotaSampleModel.start_time.desc()).limit(limit)
    return list(await db.scalars(query))


def sample_out(row: DotaSampleModel) -> dict:
    data = dict(row.data or {})
    return {
        "matchId": row.match_id,
        "heroId": row.hero_id,
        "startTime": int(row.start_time.replace(tzinfo=UTC).timestamp()),
        "duration": row.duration,
        "won": bool(data.pop("won", False)),
        "acc": data.pop("acc", None),
        "imp": data.pop("imp", None),
        "details": data,
    }


async def _prune(db: AsyncSession, hero_id: int, tier: int) -> None:
    """Keeps the newest KEEP matches."""
    keep = select(DotaSampleModel.match_id).where(DotaSampleModel.hero_id == hero_id, DotaSampleModel.tier == tier).order_by(DotaSampleModel.start_time.desc()).limit(KEEP)
    ids = list(await db.scalars(keep))
    if len(ids) >= KEEP:
        await db.execute(delete(DotaSampleModel).where(DotaSampleModel.hero_id == hero_id, DotaSampleModel.tier == tier, DotaSampleModel.match_id.not_in(ids)))


async def _more_players(db: AsyncSession, row: DotaSampleWantModel, now: datetime) -> None:
    """Players of the tier from a page of its public matches (read backwards from SETTLE ago)."""
    if row.cursor is None:
        newest = await opendota.public_matches(db, row.tier)
        if not newest:
            return
        row.cursor = max(int(m["match_id"]) for m in newest) - int(SETTLE.total_seconds() * ID_RATE)
    page = await opendota.public_matches(db, row.tier, row.cursor)
    if not page:
        return
    row.cursor = min(int(m["match_id"]) for m in page)
    seen = set(row.seen or []) | set(row.queue or [])
    found = []
    for i in range(0, len(page), stratz.MATCHES_PER_REQUEST):
        for account in await stratz.match_accounts(db, [int(m["match_id"]) for m in page[i : i + stratz.MATCHES_PER_REQUEST]]):
            medal = (account.get("seasonRank") or 0) // 10
            if account.get("id") and not account.get("isAnonymous") and medal == row.tier and account["id"] not in seen:
                seen.add(account["id"])
                found.append(int(account["id"]))
        if len(found) >= stratz.PLAYERS_PER_REQUEST:
            break
    row.queue = [*(row.queue or []), *found]


async def fill(db: AsyncSession, row: DotaSampleWantModel) -> None:
    """One step of collecting: new players of the tier when the queue runs short, then one request
    for the hero's matches of the next players in it."""
    now = utcnow()
    if now - row.requested_at > FORGET:
        row.next_check_at = None
        return
    have = await usable_count(db, row.hero_id, row.tier)
    if have >= TARGET:
        if row.filled_at is None:
            row.filled_at = now
        if now - row.filled_at < REFRESH:
            row.next_check_at = row.filled_at + REFRESH
            return
        # Time for newer matches: one step from the newest public matches (older ones go in _prune).
        row.cursor, row.filled_at, row.seen = None, None, []
    if len(row.queue or []) < stratz.PLAYERS_PER_REQUEST:
        await _more_players(db, row, now)
    players, row.queue = (row.queue or [])[: stratz.PLAYERS_PER_REQUEST], (row.queue or [])[stratz.PLAYERS_PER_REQUEST :]
    row.seen = [*(row.seen or []), *players][-SEEN:]
    known = set(await db.scalars(select(DotaSampleModel.match_id).where(DotaSampleModel.hero_id == row.hero_id, DotaSampleModel.tier == row.tier)))
    added = 0
    for match in await stratz.hero_matches(db, players, row.hero_id, now - WINDOW) if players else []:
        match_id = int(match["id"])
        data = stratz.sample_of(row.hero_id, match)
        if match_id in known or data is None or not match.get("startDateTime"):
            continue
        known.add(match_id)
        db.add(
            DotaSampleModel(
                hero_id=row.hero_id,
                tier=row.tier,
                match_id=match_id,
                start_time=datetime.fromtimestamp(int(match["startDateTime"]), UTC).replace(tzinfo=None),
                duration=int(match.get("durationSeconds") or 0),
                data=data,
            )
        )
        added += 1
    await db.flush()
    await _prune(db, row.hero_id, row.tier)
    total = have + added
    logger.info(f"[dota] rank sample hero {row.hero_id} tier {row.tier}: {len(players)} players, +{added} ({total}/{TARGET})")
    if total >= TARGET:
        row.filled_at = now
        row.next_check_at = now + REFRESH
    else:
        row.next_check_at = now + CHECK
    row.last_error = None
