"""DotaHub's death maps: which website accounts have linked Steam, checking their new ranked matches
on STRATZ, filling in their history, and the DMs the bot sends after each match."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from . import stratz
from .model import DotaMatchModel, DotaProfileModel

# A linked account is checked every CHECK minutes, more often while its history is filled in.
CHECK = timedelta(minutes=5)
BACKFILL_CHECK = timedelta(minutes=1)
RETRY = timedelta(minutes=10)
RECENT = 10
# STRATZ parses most ranked matches within minutes; one still unparsed after this is kept without
# positions (and not sent).
PARSE_WAIT = timedelta(hours=24)
# Only matches this fresh are sent (a check that was down for a day doesn't DM yesterday's games).
SEND_WINDOW = timedelta(hours=12)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def link(db: AsyncSession, site_user_id: str, steam_id: str, account_id: int, discord_id: str | None) -> DotaProfileModel:
    """The website linked (or relinked) Steam. A different Steam account starts over."""
    # The website guarantees one site account per Steam account; drop a stale row for it.
    other = await db.scalar(select(DotaProfileModel).where(DotaProfileModel.steam_id == steam_id, DotaProfileModel.site_user_id != site_user_id))
    if other is not None:
        await db.delete(other)
        await db.flush()
    row = await db.get(DotaProfileModel, site_user_id, with_for_update=True)
    if row is not None and row.steam_id != steam_id:
        await db.execute(delete(DotaMatchModel).where(DotaMatchModel.account_id == row.account_id))
        await db.delete(row)
        await db.flush()
        row = None
    if row is None:
        row = DotaProfileModel(site_user_id=site_user_id, steam_id=steam_id, account_id=account_id, linked_at=utcnow(), backfill_skip=0, backfill_done=False, notify=True)
        db.add(row)
    if discord_id:
        row.discord_id = discord_id
    row.next_check_at = utcnow()
    await db.flush()
    return row


async def unlink(db: AsyncSession, site_user_id: str) -> None:
    row = await db.get(DotaProfileModel, site_user_id, with_for_update=True)
    if row is None:
        return
    await db.execute(delete(DotaMatchModel).where(DotaMatchModel.account_id == row.account_id))
    await db.delete(row)


async def stored_count(db: AsyncSession, account_id: int) -> int:
    rows = await db.scalars(select(DotaMatchModel.match_id).where(DotaMatchModel.account_id == account_id))
    return len(rows.all())


async def recent_deaths(db: AsyncSession, account_id: int, limit: int | None) -> list[DotaMatchModel]:
    """The newest stored matches (all of them when limit is None)."""
    query = select(DotaMatchModel).where(DotaMatchModel.account_id == account_id).order_by(DotaMatchModel.start_time.desc())
    if limit:
        query = query.limit(limit)
    return list(await db.scalars(query))


def match_out(row: DotaMatchModel) -> dict:
    return {
        "matchId": row.match_id,
        "heroId": row.hero_id,
        "won": row.won,
        "startTime": int(row.start_time.replace(tzinfo=UTC).timestamp()),
        "duration": row.duration,
        "kills": row.kills,
        "deaths": row.deaths,
        "assists": row.assists,
        "imp": row.imp,
        "parsed": row.parsed,
        "deathEvents": row.death_events or [],
    }


def should_send(profile: DotaProfileModel, row: dict, now: datetime) -> bool:
    """A DM for a new match: wanted, reachable on Discord, played after linking, and fresh."""
    return bool(profile.notify and profile.discord_id and row["parsed"] and row["start_time"] >= profile.linked_at - timedelta(minutes=90) and now - row["start_time"] <= SEND_WINDOW)


async def _store(db: AsyncSession, rows: list[dict], *, notify_for: DotaProfileModel | None, now: datetime) -> int:
    """Stores the matches not stored yet; a new one is queued for a DM when notify_for wants it. A
    stored one takes what STRATZ says now (deaths, IMP, parsed), never its DM state."""
    if not rows:
        return 0
    known = {m.match_id: m for m in await db.scalars(select(DotaMatchModel).where(DotaMatchModel.account_id == rows[0]["account_id"], DotaMatchModel.match_id.in_([r["match_id"] for r in rows])))}
    added = 0
    for row in rows:
        stored = known.get(row["match_id"])
        if stored is not None:
            # A match parsed later (or read again for new fields) gets the fuller data; an unparsed
            # answer never replaces parsed data.
            if row["parsed"] or not stored.parsed:
                stored.death_events, stored.imp, stored.parsed = row["death_events"], row["imp"], row["parsed"]
            continue
        state = "pending" if notify_for is not None and should_send(notify_for, row, now) else "none"
        db.add(DotaMatchModel(**row, notify_state=state, created_at=now))
        added += 1
    await db.flush()
    return added


async def check(db: AsyncSession, profile: DotaProfileModel) -> None:
    """The newest ranked matches, then (until done) one page of history."""
    now = utcnow()
    fresh = []
    for match in await stratz.ranked_matches(db, profile.account_id, RECENT):
        row = stratz.to_row(profile.account_id, match)
        if row is None:
            continue
        # Not parsed yet: wait for STRATZ, unless it has been too long.
        if not row["parsed"] and now - row["start_time"] < PARSE_WAIT:
            continue
        fresh.append(row)
    added = await _store(db, fresh, notify_for=profile, now=now)
    if added:
        logger.info(f"[dota] {added} new ranked matches for {profile.account_id}")
    if not profile.backfill_done:
        page = await stratz.ranked_matches(db, profile.account_id, stratz.PAGE, profile.backfill_skip)
        rows = [r for r in (stratz.to_row(profile.account_id, m) for m in page) if r is not None]
        await _store(db, rows, notify_for=None, now=now)
        profile.backfill_skip += len(page)
        profile.backfill_done = len(page) < stratz.PAGE
        logger.info(f"[dota] history for {profile.account_id}: {profile.backfill_skip} read{' (done)' if profile.backfill_done else ''}")
    profile.last_error = None
    profile.next_check_at = now + (CHECK if profile.backfill_done else BACKFILL_CHECK)


async def pending(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Matches waiting for their DM, with whom to send them to."""
    rows = (
        await db.execute(
            select(DotaMatchModel, DotaProfileModel)
            .join(DotaProfileModel, DotaProfileModel.account_id == DotaMatchModel.account_id)
            .where(DotaMatchModel.notify_state == "pending")
            .order_by(DotaMatchModel.start_time)
            .limit(limit)
        )
    ).all()
    return [{**match_out(match), "accountId": match.account_id, "discordId": profile.discord_id} for match, profile in rows if profile.discord_id]


async def mark_sent(db: AsyncSession, account_id: int, match_id: int) -> bool:
    row = await db.get(DotaMatchModel, (account_id, match_id), with_for_update=True)
    if row is None:
        return False
    row.notify_state = "sent"
    return True
