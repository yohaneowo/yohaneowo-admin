"""The Binance flow: what a refresh reads (spot, funding, earn and COIN-M wallets, positions, which
symbols to follow) and how a page of fills or income is read. Binance has no "all fills" endpoint, so
spot and COIN-M fills are read symbol by symbol from a trade id; the COIN-M income and the stock
fills are one stream each, read in 200-day windows."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import store
from .binance import PAGE, Binance, KeyRejected, dec
from .common import dt_to_ms, ms_to_dt, utcnow
from .model import TradeConnectionModel, TradeFillModel, TradeIncomeModel, TradeSyncModel

# COIN-M futures opened in 2020; the income history is read from here in windows of 200 days (the
# endpoint's limit).
COINM_EPOCH_MS = 1_577_836_800_000
INCOME_WINDOW_MS = 200 * 24 * 3600 * 1000
# Binance Stocks opened in June 2026; its fills are one stream over all tickers, read in windows too.
STOCK_EPOCH_MS = 1_777_593_600_000
STOCK_WINDOW_MS = 200 * 24 * 3600 * 1000
STOCK_PAGES = 20
TRACKABLE = ("spot", "coinm")


async def check(client: Binance) -> dict:
    """The key's permissions; refused when it can withdraw or can't read."""
    permissions = await client.restrictions()
    if permissions["withdrawals"]:
        raise KeyRejected("withdrawals")
    if not permissions["reading"]:
        raise KeyRejected("reading")
    await client.spot_balances()
    return permissions


async def validate_tracked(client: Binance, product: str, symbol: str) -> bool:
    if product not in TRACKABLE:
        return False
    market = {m["id"]: m for m in (await client.markets()).values()}.get(symbol)
    return market is not None and bool(market.get("spot") if product == "spot" else market.get("inverse"))


async def refresh(db: AsyncSession, conn: TradeConnectionModel, client: Binance, now: datetime) -> None:
    funding = await client.funding_balances()
    earn = await client.earn_balances()
    # Flexible Earn holdings also show in the spot wallet as "LD" + asset: the earn rows have them.
    earned = {b["asset"] for b in earn}
    spot = [b for b in await client.spot_balances() if not (b["asset"].startswith("LD") and b["asset"][2:] in earned)]
    await store.replace_balances(db, conn.id, "spot", spot, now)
    await store.replace_balances(db, conn.id, "funding", funding, now)
    await store.replace_balances(db, conn.id, "earn", earn, now)
    held = {b["asset"] for b in spot + funding + earn}
    await store.ensure_syncs(db, conn.id, "spot", await client.spot_symbols_for(held), "balance", now)
    # Reading the futures wallet needs only "Enable Reading" (the key's futures permission is for
    # trading), so it's tried on every account; one without futures says no once, and is asked
    # again when the user refreshes by hand.
    if not conn.permissions.get("coinmUnavailable"):
        try:
            wallet = await client.coinm_wallet()
            positions = await client.coinm_positions()
        except KeyRejected:
            conn.permissions = {**conn.permissions, "coinmUnavailable": True}
        else:
            await store.replace_balances(db, conn.id, "coinm", wallet, now)
            await store.replace_positions(db, conn.id, "coinm", [position_from(p) for p in positions], now)
            await store.ensure_syncs(db, conn.id, "coinm", [store.STREAM], "income", now)
            await store.ensure_syncs(db, conn.id, "coinm", sorted({p["symbol"] for p in positions}), "position", now)
            seen = await db.scalars(
                select(TradeIncomeModel.symbol).where(TradeIncomeModel.connection_id == conn.id, TradeIncomeModel.product == "coinm", TradeIncomeModel.symbol.is_not(None)).distinct()
            )
            await store.ensure_syncs(db, conn.id, "coinm", sorted(set(seen)), "income", now)
    # Stocks (held as EQ_<ticker>, mostly in the funding wallet): one stream over all tickers.
    await store.ensure_syncs(db, conn.id, "stock", [store.STREAM], "stock", now)


def position_from(p: dict) -> dict:
    """A row of GET /dapi/v1/positionRisk as a position."""
    return {
        "symbol": p["symbol"],
        "position_side": p.get("positionSide") or "BOTH",
        "qty": dec(p["positionAmt"]),
        "entry_price": dec(p.get("entryPrice")) or None,
        "mark_price": dec(p.get("markPrice")) or None,
        "unrealized_pnl": dec(p.get("unRealizedProfit")),
        "liquidation_price": dec(p.get("liquidationPrice")) or None,
        "leverage": int(p["leverage"]) if p.get("leverage") else None,
        "margin_type": p.get("marginType"),
        "raw": p,
    }


# ── Fills and income, a page a time ──────────────────────────────────────────────────────────


def fill_from(product: str, symbol: str, r: dict) -> TradeFillModel:
    """A Binance trade (spot myTrades or dapi userTrades) as a row."""
    if product == "spot":
        side = "BUY" if r["isBuyer"] else "SELL"
        quote_qty, maker, pnl, position_side = dec(r["quoteQty"]), bool(r["isMaker"]), None, None
    else:
        side = r["side"]
        quote_qty, maker, pnl, position_side = dec(r.get("baseQty")), bool(r.get("maker")), dec(r.get("realizedPnl")), r.get("positionSide")
    return TradeFillModel(
        product=product,
        symbol=symbol,
        fill_id=str(r["id"]),
        order_id=str(r["orderId"]),
        time=ms_to_dt(r["time"]),
        side=side,
        price=dec(r["price"]),
        qty=dec(r["qty"]),
        quote_qty=quote_qty,
        fee=dec(r.get("commission")),
        fee_asset=r.get("commissionAsset") or "",
        maker=maker,
        realized_pnl=pnl,
        position_side=position_side,
        raw=r,
    )


def stock_fill_from(r: dict, fee: Decimal) -> TradeFillModel:
    """A Binance Stocks execution as a row; the fee is this execution's share of its order's fee."""
    return TradeFillModel(
        product="stock",
        symbol=r["symbol"],
        fill_id=str(r["executionId"]),
        order_id=str(r["orderId"]),
        time=ms_to_dt(r["executionAt"]),
        side=r["side"],
        price=dec(r["price"]),
        qty=dec(r["qty"]),
        quote_qty=dec(r.get("total")),
        fee=fee,
        fee_asset=r.get("quote") or "USDC",
        maker=False,
        realized_pnl=None,
        position_side=None,
        raw=r,
    )


async def _stock_window(client: Binance, start: int, end: int) -> list[TradeFillModel]:
    """Every stock fill in a window, with the orders' fees shared out by quantity."""
    rows: list[dict] = []
    for page in range(1, STOCK_PAGES + 1):
        chunk, total = await client.stock_trades(start, end, page)
        rows.extend(chunk)
        if not chunk or len(rows) >= total:
            break
    if not rows:
        return []
    orders: dict[str, dict] = {}
    for page in range(1, STOCK_PAGES + 1):
        chunk, total = await client.stock_orders(start, end, page)
        orders.update({str(o["orderId"]): o for o in chunk})
        if not chunk or len(orders) >= total:
            break
    fills = []
    for r in rows:
        order = orders.get(str(r["orderId"]))
        filled = dec(order.get("filledQty")) if order else Decimal(0)
        fee = (dec(order.get("fee")) * dec(r["qty"]) / filled).quantize(Decimal("1E-18")) if order and filled else Decimal(0)
        fills.append(stock_fill_from(r, fee))
    return fills


async def sync_page(db: AsyncSession, conn: TradeConnectionModel, sync: TradeSyncModel, client: Binance) -> int:
    """Reads one page for a sync row and moves its cursor; returns how many new rows were stored.
    A short page means the history is in: the next look is in REFRESH, else right away."""
    now = utcnow()
    now_ms = dt_to_ms(now)
    if sync.product == "stock":
        # One window a call (all its pages); the cursor is the window's start.
        start = sync.cursor or STOCK_EPOCH_MS
        end = min(start + STOCK_WINDOW_MS, now_ms)
        fills = await _stock_window(client, start, end)
        new = await store.store_fills(db, conn.id, fills)
        store.note_times(sync, [f.time for f in fills])
        # The last window is read again from its start next time (fills are deduplicated), so a
        # fill that lands late is still picked up.
        sync.cursor = end if end < now_ms else start
        short = end >= now_ms
    elif sync.symbol == store.STREAM:
        start = sync.cursor or COINM_EPOCH_MS
        # Up to ten empty windows a page: an account that opened futures recently has years of nothing.
        for _ in range(10):
            end = min(start + INCOME_WINDOW_MS, now_ms)
            rows = await client.coinm_income(start, end)
            if rows or end >= now_ms:
                break
            start = end
        new = await _store_income(db, conn.id, sync.product, rows)
        store.note_times(sync, [ms_to_dt(r["time"]) for r in rows])
        # A full page: continue from its last moment (that moment again, deduplicated); else the
        # window is done.
        sync.cursor = max(int(r["time"]) for r in rows) if len(rows) >= PAGE else end
        short = len(rows) < PAGE and end >= now_ms
    else:
        read = client.spot_trades if sync.product == "spot" else client.coinm_trades
        rows = await read(sync.symbol, sync.cursor or 0)
        fills = [fill_from(sync.product, sync.symbol, r) for r in rows]
        new = await store.store_fills(db, conn.id, fills)
        if rows:
            sync.cursor = max(int(r["id"]) for r in rows) + 1
        store.note_times(sync, [f.time for f in fills])
        short = len(rows) < PAGE
    sync.count += new
    sync.last_ok_at = now
    sync.last_error = None
    sync.backfilled = sync.backfilled or short
    sync.next_at = now + store.REFRESH if short else now
    return new


async def _store_income(db: AsyncSession, connection_id: int, product: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    keys = [(int(r["tranId"]), r["incomeType"]) for r in rows]
    have = set(
        (
            await db.execute(
                select(TradeIncomeModel.income_id, TradeIncomeModel.type).where(
                    TradeIncomeModel.connection_id == connection_id, TradeIncomeModel.product == product, TradeIncomeModel.income_id.in_([k[0] for k in keys])
                )
            )
        )
        .tuples()
        .all()
    )
    new = 0
    for r, key in zip(rows, keys, strict=True):
        if key in have:
            continue
        db.add(
            TradeIncomeModel(
                connection_id=connection_id,
                product=product,
                income_id=key[0],
                type=key[1],
                symbol=r.get("symbol") or None,
                asset=r.get("asset") or "",
                amount=dec(r.get("income")),
                time=ms_to_dt(r["time"]),
                raw=r,
            )
        )
        have.add(key)
        new += 1
    await db.flush()
    return new
