"""The database side the exchange flows share: replacing balances, positions and bots, making sure a
symbol has a sync row, storing fills without duplicates, and the timing constants."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import TradeBalanceModel, TradeBotModel, TradeFillModel, TradePositionModel, TradeSyncModel

# Balances, positions and the tracked list are refreshed this often; so are symbols whose history is in.
REFRESH = timedelta(minutes=5)
RETRY = timedelta(minutes=10)
# A key that was rejected is tried again this much later (the user may have fixed it).
KEY_RETRY = timedelta(hours=1)
# The symbol of a sync row that is a stream over all symbols of a product, paged by time.
STREAM = "*"


async def replace_balances(db: AsyncSession, connection_id: int, wallet: str, rows: list[dict], now: datetime) -> None:
    """Rows are {asset, free, locked, total?, unrealized?}."""
    await db.execute(delete(TradeBalanceModel).where(TradeBalanceModel.connection_id == connection_id, TradeBalanceModel.wallet == wallet))
    for b in rows:
        total = b.get("total", b["free"] + b["locked"])
        db.add(TradeBalanceModel(connection_id=connection_id, wallet=wallet, asset=b["asset"], free=b["free"], locked=b["locked"], total=total, unrealized=b.get("unrealized"), updated_at=now))


async def replace_positions(db: AsyncSession, connection_id: int, product: str, rows: list[dict], now: datetime) -> None:
    """Rows are {symbol, position_side, qty, entry_price, mark_price, unrealized_pnl, liquidation_price, leverage, margin_type, raw}."""
    await db.execute(delete(TradePositionModel).where(TradePositionModel.connection_id == connection_id, TradePositionModel.product == product))
    for p in rows:
        db.add(TradePositionModel(connection_id=connection_id, product=product, updated_at=now, **p))


async def replace_bots(db: AsyncSession, connection_id: int, running: list[TradeBotModel], finished: list[TradeBotModel]) -> None:
    """Running bots replace the running set (one that disappeared without showing up as finished is
    dropped); finished ones are upserted and kept."""
    await db.execute(delete(TradeBotModel).where(TradeBotModel.connection_id == connection_id, TradeBotModel.status == "running"))
    ids = [b.bot_id for b in finished]
    if ids:
        await db.execute(delete(TradeBotModel).where(TradeBotModel.connection_id == connection_id, TradeBotModel.bot_id.in_(ids)))
    await db.flush()
    for b in [*running, *finished]:
        b.connection_id = connection_id
        db.add(b)
    await db.flush()


async def ensure_syncs(db: AsyncSession, connection_id: int, product: str, symbols: list[str], source: str, now: datetime) -> None:
    if not symbols:
        return
    have = set(await db.scalars(select(TradeSyncModel.symbol).where(TradeSyncModel.connection_id == connection_id, TradeSyncModel.product == product, TradeSyncModel.symbol.in_(symbols))))
    for symbol in symbols:
        if symbol not in have:
            db.add(TradeSyncModel(connection_id=connection_id, product=product, symbol=symbol, source=source, next_at=now))
    await db.flush()


async def store_fills(db: AsyncSession, connection_id: int, fills: list[TradeFillModel]) -> int:
    """Stores the fills that aren't there yet; returns how many. Fill ids are unique within a symbol
    (or globally), so the pair is the key."""
    if not fills:
        return 0
    product = fills[0].product
    have = set(
        (
            await db.execute(
                select(TradeFillModel.symbol, TradeFillModel.fill_id).where(
                    TradeFillModel.connection_id == connection_id, TradeFillModel.product == product, TradeFillModel.fill_id.in_([f.fill_id for f in fills])
                )
            )
        )
        .tuples()
        .all()
    )
    new = 0
    for f in fills:
        if (f.symbol, f.fill_id) not in have:
            f.connection_id = connection_id
            db.add(f)
            have.add((f.symbol, f.fill_id))
            new += 1
    await db.flush()
    return new


def note_times(sync: TradeSyncModel, times: list[datetime]) -> None:
    """Widens the sync row's oldest / newest fill times."""
    if not times:
        return
    sync.oldest_time = min([sync.oldest_time, *times]) if sync.oldest_time else min(times)
    sync.newest_time = max([sync.newest_time, *times]) if sync.newest_time else max(times)


def positive(value: Decimal) -> Decimal:
    return value if value > 0 else Decimal(0)
