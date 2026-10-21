"""DotaHub guests: the whole ranked history of an account nobody linked, read because a signed-in
website user asked (and confirmed) — so the economy, IMP and death-map pages can show 「全部」 for
anyone. One guest at a time, a STRATZ page (100 matches) a minute, within a daily budget, after the
linked accounts; the matches go where linked accounts' do. A guest unseen for a month is dropped."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from . import service, stratz
from .model import DotaGuestModel, DotaMatchModel, DotaProfileModel

# A page of history a minute, and at most this many pages a day for all guests together (STRATZ's
# quota is shared with the linked accounts' checks, which come first).
PAGE_EVERY = timedelta(minutes=1)
DAILY_PAGES = 300
# Each website account may have this many waiting or being read at once.
PER_USER = 3
# A finished guest's newest matches are read again when it's shown, at most this often.
REFRESH = timedelta(hours=1)
# Dropped (with its matches) after this long unseen.
KEEP = timedelta(days=30)
RETRY = timedelta(minutes=10)

# Pages read today (kept in memory: a restart only allows a few more).
_budget: tuple[date, int] = (date.min, 0)


def _spend(now: datetime) -> bool:
    """Takes a page from today's budget; False when it's used up."""
    global _budget
    day, used = _budget
    if day != now.date():
        day, used = now.date(), 0
    if used >= DAILY_PAGES:
        _budget = (day, used)
        return False
    _budget = (day, used + 1)
    return True


async def position(db: AsyncSession, guest: DotaGuestModel) -> int:
    """How many unfinished guests were asked for before this one (0: it's being read)."""
    if guest.backfill_done:
        return 0
    # Not itself: MySQL keeps whole seconds, so a fresh row can read as earlier than its own time.
    return int(
        await db.scalar(
            select(func.count())
            .select_from(DotaGuestModel)
            .where(DotaGuestModel.backfill_done.is_(False), DotaGuestModel.account_id != guest.account_id, DotaGuestModel.requested_at < guest.requested_at)
        )
        or 0
    )


async def status(db: AsyncSession, guest: DotaGuestModel) -> dict:
    ahead = await position(db, guest)
    return {
        "accountId": guest.account_id,
        "state": "done" if guest.backfill_done else "reading" if ahead == 0 and guest.started_at else "queued",
        "ahead": ahead,
        "read": guest.backfill_skip,
        "historyDone": guest.backfill_done,
        "requestedAt": int(guest.requested_at.replace(tzinfo=UTC).timestamp()),
        "error": guest.last_error,
    }


async def request(db: AsyncSession, account_id: int, requested_by: str, now: datetime) -> DotaGuestModel | str:
    """Queues an account; "linked" when it's a linked account already (its history is being read
    anyway), "tooMany" when the website account has PER_USER waiting."""
    if await db.scalar(select(DotaProfileModel.site_user_id).where(DotaProfileModel.account_id == account_id)) is not None:
        return "linked"
    guest = await db.get(DotaGuestModel, account_id, with_for_update=True)
    if guest is not None:
        guest.viewed_at = now
        return guest
    waiting = await db.scalar(select(func.count()).select_from(DotaGuestModel).where(DotaGuestModel.requested_by == requested_by, DotaGuestModel.backfill_done.is_(False)))
    if (waiting or 0) >= PER_USER:
        return "tooMany"
    # Whole seconds, as MySQL stores them.
    now = now.replace(microsecond=0)
    guest = DotaGuestModel(account_id=account_id, requested_by=requested_by, requested_at=now, viewed_at=now, backfill_skip=0, backfill_done=False)
    db.add(guest)
    await db.flush()
    return guest


def seen(guest: DotaGuestModel, now: datetime) -> None:
    """Shown on the website: keep it, and read its newest matches when they're stale."""
    guest.viewed_at = now
    if guest.backfill_done and guest.next_check_at is None and (guest.refreshed_at is None or now - guest.refreshed_at >= REFRESH):
        guest.next_check_at = now


async def _drop_unseen(db: AsyncSession, now: datetime) -> None:
    for guest in await db.scalars(select(DotaGuestModel).where(DotaGuestModel.viewed_at < now - KEEP).limit(5)):
        # A guest that has since linked keeps its matches (they're the linked account's now).
        if await db.scalar(select(DotaProfileModel.site_user_id).where(DotaProfileModel.account_id == guest.account_id)) is None:
            await db.execute(delete(DotaMatchModel).where(DotaMatchModel.account_id == guest.account_id))
        await db.delete(guest)
        logger.info(f"[dota] guest {guest.account_id} dropped (unseen)")


async def _rows(db: AsyncSession, account_id: int, take: int, skip: int = 0) -> tuple[int, list[dict]]:
    page = await stratz.ranked_matches(db, account_id, take, skip)
    return len(page), [r for r in (stratz.to_row(account_id, m) for m in page) if r is not None]


async def tick(db: AsyncSession, now: datetime) -> None:
    """One step: drop the unseen, refresh a finished guest that was shown, read a page for the
    first guest in line (when its minute is up and the day's budget allows)."""
    await _drop_unseen(db, now)
    refresh = await db.scalar(
        select(DotaGuestModel)
        .where(DotaGuestModel.backfill_done.is_(True), DotaGuestModel.next_check_at.is_not(None), DotaGuestModel.next_check_at <= now)
        .order_by(DotaGuestModel.next_check_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if refresh is not None and _spend(now):
        try:
            _, rows = await _rows(db, refresh.account_id, service.RECENT)
            await service._store(db, rows, notify_for=None, now=now)
            refresh.refreshed_at, refresh.next_check_at, refresh.last_error = now, None, None
        except Exception as error:  # noqa: BLE001 - a guest's failure never stops the loop
            refresh.last_error, refresh.next_check_at = str(error)[:500], now + RETRY
    guest = await db.scalar(select(DotaGuestModel).where(DotaGuestModel.backfill_done.is_(False)).order_by(DotaGuestModel.requested_at).limit(1).with_for_update(skip_locked=True))
    if guest is None or (guest.next_check_at is not None and guest.next_check_at > now) or not _spend(now):
        return
    try:
        count, rows = await _rows(db, guest.account_id, stratz.PAGE, guest.backfill_skip)
        await service._store(db, rows, notify_for=None, now=now)
        guest.started_at = guest.started_at or now
        guest.backfill_skip += count
        guest.backfill_done = count < stratz.PAGE
        guest.last_error = None
        guest.next_check_at = None if guest.backfill_done else now + PAGE_EVERY
        if guest.backfill_done:
            guest.refreshed_at = now
        logger.info(f"[dota] guest history for {guest.account_id}: {guest.backfill_skip} read{' (done)' if guest.backfill_done else ''}")
    except Exception as error:  # noqa: BLE001
        guest.last_error, guest.next_check_at = str(error)[:500], now + RETRY
        logger.warning(f"[dota] guest {guest.account_id} failed: {error}")
