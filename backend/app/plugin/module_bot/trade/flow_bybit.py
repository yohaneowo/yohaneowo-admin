"""The Bybit flow: the unified, funding and earn wallets, the open contract positions, and the fills
as one stream per product (spot, usdm = linear, coinm = inverse), read in 7-day windows from two years
back, with Bybit's page cursor inside a window. No symbol list to maintain, so nothing is trackable
by hand."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from . import store
from .bybit import CATEGORIES, HISTORY_MS, PAGE, WINDOW_MS, Bybit
from .common import KeyRejected, dec, dt_to_ms, ms_to_dt, utcnow
from .model import TradeConnectionModel, TradeFillModel, TradeSyncModel

PRODUCT_CATEGORY = {product: category for category, product in CATEGORIES.items()}
# When a window reaches now, the next read starts this much before its end: a fill that lands late
# is still seen (fills are deduplicated).
OVERLAP_MS = 60_000
# Empty windows skipped in one call (two years of a product never traded is 104 of them).
EMPTY_WINDOWS = 10


async def check(client: Bybit) -> dict:
    permissions = await client.restrictions()
    if permissions["withdrawals"]:
        raise KeyRejected("withdrawals")
    if not permissions["unified"]:
        raise KeyRejected("classic")
    await client.unified_balances()
    return permissions


async def validate_tracked(client: Bybit, product: str, symbol: str) -> bool:
    # Fills come as whole-category streams; there is nothing to follow symbol by symbol.
    return False


async def refresh(db: AsyncSession, conn: TradeConnectionModel, client: Bybit, now: datetime) -> None:
    # Bybit's own prices value every coin it lists (Binance, which the website prices with
    # otherwise, lacks many of the small ones); the unified rows carry Bybit's own valuation.
    prices = await client.prices()
    priced = lambda rows: [{**b, "price_usd": b.get("price_usd") or prices.get(b["asset"])} for b in rows]  # noqa: E731
    await store.replace_balances(db, conn.id, "unified", priced(await client.unified_balances()), now)
    await store.replace_balances(db, conn.id, "funding", priced(await client.fund_balances()), now)
    await store.replace_balances(db, conn.id, "earn", priced(await client.earn_balances()), now)
    positions = await client.positions()
    for product in ("usdm", "coinm"):
        await store.replace_positions(db, conn.id, product, [position_from(p) for p in positions if p["_product"] == product], now)
    for product in CATEGORIES.values():
        await store.ensure_syncs(db, conn.id, product, [store.STREAM], "stream", now)


def position_from(p: dict) -> dict:
    """A row of GET /v5/position/list as a position; a short is a negative quantity."""
    qty = dec(p.get("size"))
    if p.get("side") == "Sell":
        qty = -qty
    return {
        "symbol": p["symbol"],
        "position_side": {"1": "LONG", "2": "SHORT"}.get(str(p.get("positionIdx")), "BOTH"),
        "qty": qty,
        "entry_price": dec(p.get("avgPrice")) or None,
        "mark_price": dec(p.get("markPrice")) or None,
        "unrealized_pnl": dec(p.get("unrealisedPnl")),
        "liquidation_price": dec(p.get("liqPrice")) or None,
        "leverage": int(dec(p["leverage"])) if p.get("leverage") else None,
        "margin_type": {"0": "cross", "1": "isolated"}.get(str(p.get("tradeMode"))),
        "raw": {k: v for k, v in p.items() if k != "_product"},
    }


def fill_from(product: str, r: dict) -> TradeFillModel:
    """A row of GET /v5/execution/list as a fill. Spot quantities are in the base coin; linear
    contracts in coins too (one contract = one coin); inverse contracts in USD-sized contracts with
    the value in the coin."""
    symbol = r["symbol"]
    return TradeFillModel(
        product=product,
        symbol=symbol,
        fill_id=str(r["execId"]),
        order_id=str(r.get("orderId") or ""),
        time=ms_to_dt(r["execTime"]),
        side="BUY" if r.get("side") == "Buy" else "SELL",
        price=dec(r.get("execPrice")),
        qty=dec(r.get("execQty")),
        quote_qty=dec(r.get("execValue")),
        fee=dec(r.get("execFee")),
        fee_asset=r.get("feeCurrency") or settle_coin(product, symbol),
        maker=bool(r.get("isMaker")),
        realized_pnl=None,
        position_side=None,
        raw=r,
    )


def settle_coin(product: str, symbol: str) -> str:
    """What a contract's fee is paid in when the row doesn't say: linear in its stable, inverse in
    its base coin."""
    if product == "usdm":
        return "USDC" if symbol.endswith("PERP") or symbol.endswith("USDC") else "USDT"
    if product == "coinm":
        return symbol.split("USD")[0]
    return ""


async def sync_page(db: AsyncSession, conn: TradeConnectionModel, sync: TradeSyncModel, client: Bybit) -> int:
    """Reads one page of a product's stream: the current 7-day window from the cursor (the window's
    start), continuing with Bybit's page cursor while the window has more; empty windows are skipped
    several at a time. Once a window reaches now, the history is in and the stream is looked at
    every REFRESH from a minute before where it stopped."""
    now = utcnow()
    now_ms = dt_to_ms(now)
    category = PRODUCT_CATEGORY[sync.product]
    start = sync.cursor or (now_ms - HISTORY_MS)
    rows: list[dict] = []
    next_cursor = None
    for _ in range(EMPTY_WINDOWS):
        end = min(start + WINDOW_MS, now_ms)
        rows, next_cursor = await client.executions(category, start, end, sync.page_cursor)
        if rows or next_cursor or end >= now_ms:
            break
        start = end
    fills = [fill_from(sync.product, r) for r in rows]
    new = await store.store_fills(db, conn.id, fills)
    store.note_times(sync, [f.time for f in fills])
    sync.count += new
    sync.last_ok_at = now
    sync.last_error = None
    if next_cursor:
        # More pages in this window.
        sync.cursor = start
        sync.page_cursor = next_cursor
        sync.next_at = now
    elif end >= now_ms:
        # Caught up: next time from a little before now.
        sync.cursor = max(start, end - OVERLAP_MS)
        sync.page_cursor = None
        sync.backfilled = True
        sync.next_at = now + store.REFRESH
    else:
        sync.cursor = end
        sync.page_cursor = None
        sync.next_at = now
    return new


__all__ = ["PAGE", "check", "refresh", "sync_page", "validate_tracked"]
