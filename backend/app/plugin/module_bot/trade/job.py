"""The TradeHub loop: every TICK seconds, refresh the connections that are due (balances, positions,
which symbols to read) and read a few pages of fills. Started by the first TradeHub request, like the
movie and DotaHub loops; with several workers each runs a loop and SKIP LOCKED keeps one reader per
row. All connections share this host's request budget at Binance (weight 6,000 a minute per IP; a
page of fills is 20), so the pages a tick are capped."""

import asyncio
from itertools import groupby

from sqlalchemy import or_, select

from app.core.database import async_db_session
from app.core.logger import logger

from ..movie.sources.base import SourceUnavailable
from . import service
from .binance import KeyRejected
from .model import TradeConnectionModel, TradeSyncModel

TICK = 30
CONNECTIONS_PER_TICK = 3
PAGES_PER_TICK = 15

_task: asyncio.Task | None = None


async def run_once() -> int:
    """Refreshes due connections and reads due pages; returns how many pages were read."""
    now = service.utcnow()
    async with async_db_session() as db, db.begin():
        due = list(
            await db.scalars(
                select(TradeConnectionModel)
                .where(TradeConnectionModel.status != "disabled", or_(TradeConnectionModel.next_check_at.is_(None), TradeConnectionModel.next_check_at <= now))
                .order_by(TradeConnectionModel.next_check_at)
                .limit(CONNECTIONS_PER_TICK)
                .with_for_update(skip_locked=True)
            )
        )
        for conn in due:
            try:
                await service.refresh(db, conn)
            except KeyRejected as error:
                service.key_failed(conn, error, now)
                logger.warning(f"[trade] key rejected for connection {conn.id}: {error}")
            except (SourceUnavailable, Exception) as error:  # noqa: BLE001 - one account's failure must not stop the others
                conn.last_error = str(error)[:500]
                conn.next_check_at = now + service.RETRY
                logger.warning(f"[trade] refresh failed for connection {conn.id}: {error}")
    pages = 0
    async with async_db_session() as db, db.begin():
        rows = list(
            await db.scalars(
                select(TradeSyncModel)
                .join(TradeConnectionModel, TradeConnectionModel.id == TradeSyncModel.connection_id)
                .where(TradeConnectionModel.status == "ok", TradeSyncModel.next_at.is_not(None), TradeSyncModel.next_at <= now)
                .order_by(TradeSyncModel.next_at, TradeSyncModel.connection_id)
                .limit(PAGES_PER_TICK)
                .with_for_update(skip_locked=True, of=TradeSyncModel)
            )
        )
        for connection_id, group in groupby(sorted(rows, key=lambda s: (s.connection_id, s.product, s.symbol)), key=lambda s: s.connection_id):
            conn = await db.get(TradeConnectionModel, connection_id)
            if conn is None:
                continue
            client = service.client_for(conn)
            try:
                for sync in group:
                    try:
                        await service.sync_page(db, conn, sync, client)
                        pages += 1
                    except KeyRejected as error:
                        service.key_failed(conn, error, now)
                        logger.warning(f"[trade] key rejected for connection {conn.id}: {error}")
                        break
                    except (SourceUnavailable, Exception) as error:  # noqa: BLE001 - one symbol's failure must not stop the others
                        sync.last_error = str(error)[:500]
                        sync.next_at = now + service.RETRY
                        logger.warning(f"[trade] {sync.product} {sync.symbol} of connection {conn.id} failed: {error}")
            finally:
                await client.close()
    return pages


async def _loop() -> None:
    while True:
        try:
            await run_once()
        except Exception as error:  # noqa: BLE001 - the loop must survive database hiccups
            logger.warning(f"[trade] loop error: {error}")
        await asyncio.sleep(TICK)


async def ensure_loop() -> None:
    """Router dependency: start the loop in this process if it isn't running."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="trade-sync")
        logger.info("[trade] loop started.")
