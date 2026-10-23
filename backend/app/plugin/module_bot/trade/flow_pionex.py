"""The Pionex flow. Three accounts: the trading account (wallet "spot": manual spot, fills per
symbol), the futures account (wallet "usdm": manual contracts, positions and fills per symbol) and
the bot account (wallet "bot": every bot's investment and profit, plus the coins parked there), where
grid bots run — their positions and profit come from the bot-order list (bot_trade_bot); their fills
aren't exposed by the API. Fills page by time: the latest 100 (spot) or 200 (futures) of a window,
read backwards until a short page, then forwards from the newest fill."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from . import store
from .common import dec, dt_to_ms, ms_to_dt, utcnow
from .model import TradeBotModel, TradeConnectionModel, TradeFillModel, TradeSyncModel
from .pionex import FINISHED, RUNNING, SPOT_PAGE, USDM_PAGE, Pionex, perp_symbol

QUOTES = ("USDT", "USDC")
STABLES = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "USD1"}
TRACKABLE = ("spot", "usdm")
# Finished bots: the newest pages are read on every refresh (older ones were stored before).
FINISHED_PAGES = 2


async def check(client: Pionex) -> dict:
    return await client.restrictions()


async def validate_tracked(client: Pionex, product: str, symbol: str) -> bool:
    if product not in TRACKABLE:
        return False
    rows = await client.symbols("SPOT" if product == "spot" else "PERP")
    return any(s.get("symbol") == symbol for s in rows)


async def refresh(db: AsyncSession, conn: TradeConnectionModel, client: Pionex, now: datetime) -> None:
    overview = await client.overview()
    # The overview carries Pionex's USD price of every coin held, for coins Binance doesn't list.
    prices = {coin: dec(p.get("priceInUsd")) for coin, p in (overview.get("prices") or {}).items() if isinstance(p, dict) and p.get("priceInUsd")}
    prices.setdefault("USDT", dec(1))
    priced = lambda rows: [{**b, "price_usd": prices.get(b["asset"])} for b in rows]  # noqa: E731
    spot = await client.spot_balances()
    await store.replace_balances(db, conn.id, "spot", priced(spot), now)
    bot_wallet, earn = _bot_account(overview)
    await store.replace_balances(db, conn.id, "bot", priced(bot_wallet), now)
    await store.replace_balances(db, conn.id, "earn", priced(earn), now)
    await store.replace_balances(db, conn.id, "usdm", priced(await client.usdm_balances()), now)
    positions = await client.usdm_positions()
    await store.replace_positions(db, conn.id, "usdm", [position_from(p) for p in positions], now)
    if conn.permissions.get("botReading", True):
        running = [bot_from(o, "running") for o in await client.bots(RUNNING)]
        running_ids = {b.bot_id for b in running}
        finished = [bot_from(o, "finished") for o in await client.bots(FINISHED, pages=FINISHED_PAGES) if o.get("closeTime") and str(o.get("buOrderId")) not in running_ids]
        await store.replace_bots(db, conn.id, running, finished)
    held = {b["asset"] for b in spot} | {b["asset"] for b in bot_wallet if not b.get("bot")}
    await store.ensure_syncs(db, conn.id, "spot", await _spot_symbols(client, held), "balance", now)
    await store.ensure_syncs(db, conn.id, "usdm", sorted({p["symbol"] for p in positions}), "position", now)


def _bot_account(overview: dict) -> tuple[list[dict], list[dict]]:
    """The bot account of GET /api/v1/wallet/balancesFull as balances: the bots summed per
    investment coin (what each is worth now: what was put in plus the profit so far — the profit
    itself is on the bot rows, not repeated as "unrealized", which the website adds to the total),
    the spot coins parked in the bot account, and the earn products as a wallet of their own."""
    bots: dict[str, dict] = {}
    coins: dict[str, dict] = {}
    earn: dict[str, Decimal] = {}
    for detail in (overview.get("botAccount") or {}).get("detail") or []:
        kind = detail.get("type")
        for item in detail.get("list") or []:
            if kind == "trading_bot":
                asset = item.get("investmentToken") or "USDT"
                worth = dec(item.get("investmentAmount")) + dec(item.get("profit"))
                row = bots.setdefault(asset, {"asset": asset, "free": Decimal(0), "locked": Decimal(0), "total": Decimal(0), "bot": True})
                row["locked"] += worth
                row["total"] += worth
            elif kind == "spot":
                asset = item.get("coin")
                free = dec(item.get("free"))
                frozen = dec(item.get("frozen")) + dec(item.get("frozenByOrder")) + dec(item.get("frozenByMoveout")) + dec(item.get("frozenByCoinout"))
                if asset and (free or frozen):
                    row = coins.setdefault(asset, {"asset": asset, "free": Decimal(0), "locked": Decimal(0)})
                    row["free"] += free
                    row["locked"] += frozen
            elif kind == "lending":
                asset = item.get("coin")
                amount = dec(item.get("amount") or item.get("assets") or item.get("total"))
                if asset and amount:
                    earn[asset] = earn.get(asset, Decimal(0)) + amount
    # A coin both invested in a bot and parked: one row, the parked part spendable.
    for asset, row in coins.items():
        bot = bots.get(asset)
        if bot:
            bot["free"] += row["free"]
            bot["locked"] += row["locked"]
            bot["total"] += row["free"] + row["locked"]
        else:
            bots[asset] = row
    return list(bots.values()), [{"asset": asset, "free": Decimal(0), "locked": amount} for asset, amount in earn.items()]


async def _spot_symbols(client: Pionex, assets: set[str]) -> list[str]:
    ids = {s.get("symbol") for s in await client.symbols("SPOT") if s.get("enable", True)}
    out = []
    for asset in sorted(assets):
        if asset in STABLES:
            continue
        out.extend(f"{asset}_{quote}" for quote in QUOTES if f"{asset}_{quote}" in ids)
    return out


def position_from(p: dict) -> dict:
    """A row of GET /uapi/v1/account/positions as a position (net size, short negative)."""
    qty = dec(p.get("netSize"))
    if not qty:
        qty = dec(p.get("sizeLong")) - dec(p.get("sizeShort"))
    side = str(p.get("positionSide") or "").upper()
    return {
        "symbol": p["symbol"],
        "position_side": side if side in ("LONG", "SHORT") else "BOTH",
        "qty": qty,
        "entry_price": dec(p.get("avgPrice")) or None,
        "mark_price": dec(p.get("markPrice")) or None,
        "unrealized_pnl": dec(p.get("unrealizedPnL")),
        "liquidation_price": dec(p.get("liquidationPrice")) or None,
        "leverage": int(dec(p["leverage"])) if p.get("leverage") else None,
        "margin_type": "isolated" if str(p.get("isolatedMode", "")).upper() == "ISOLATED" else "cross",
        "raw": p,
    }


def bot_from(o: dict, status: str) -> TradeBotModel:
    """A bot order of GET /api/v1/bot/orders as a row. The fields differ by type: futures grids
    have a position and its open price, spot grids a base amount and an average cost, hedge grids
    only totals."""
    d = o.get("buOrderData") or {}
    kind = o.get("buOrderType") or ""
    base, quote = o.get("base") or "", o.get("quote") or "USDT"
    futures = base.endswith(".PERP")
    symbol = perp_symbol(base, quote) if futures else f"{base}_{quote}"
    trend = d.get("trend")
    liquidation = dec(d.get("liquidationPrice")) or dec(d.get("estimateLiquidationPriceUp") if trend == "short" else d.get("estimateLiquidationPriceDown"))
    return TradeBotModel(
        bot_id=str(o["buOrderId"]),
        type=kind[:24],
        name=(o.get("customizeName") or o.get("botName") or "")[:64],
        symbol=symbol,
        base=base.removesuffix(".PERP")[:16],
        quote=quote[:16],
        status=status,
        state=(d.get("status") or o.get("status") or None),
        side=(trend if trend in ("long", "short", "neutral") else None),
        leverage=int(dec(d["leverage"])) if d.get("leverage") else None,
        # A futures grid's investment is the grid's own plus the extra margin, both in the quote.
        investment=_sum(d, "quoteInvestment", "extraMargin") or _first(d, "initQuoteInvestment", "usdtInvestment"),
        margin=_first(d, "marginBalance"),
        qty=_first(d, "position", "baseAmount"),
        entry_price=_first(d, "positionOpenPrice", "averageCost"),
        grid_profit=_first(d, "gridProfit"),
        realized_pnl=_first(d, "totalRealizedProfit", "realizedProfit"),
        fee=_first(d, "totalFee", "totalFeeInQuote"),
        funding=_first(d, "totalFundingFee", "fundingFeePayment"),
        pairs=int(dec(d["exchangeOrderPairedCount"])) if d.get("exchangeOrderPairedCount") not in (None, "") else None,
        top=_first(d, "top"),
        bottom=_first(d, "bottom"),
        rows=int(dec(d["row"])) if d.get("row") not in (None, "") else None,
        liquidation_price=liquidation or None,
        created_at=ms_to_dt(o.get("createTime") or d.get("createTime") or 0),
        closed_at=ms_to_dt(o["closeTime"]) if o.get("closeTime") else None,
        raw={"order": {k: v for k, v in o.items() if k != "buOrderData"}, "data": d, "reasonBy": d.get("reasonBy")},
    )


def _first(d: dict, *keys: str) -> Decimal | None:
    for key in keys:
        if d.get(key) not in (None, ""):
            return dec(d[key])
    return None


def _sum(d: dict, *keys: str) -> Decimal | None:
    """The sum of the keys that are present; None when none is."""
    values = [dec(d[key]) for key in keys if d.get(key) not in (None, "")]
    return sum(values, Decimal(0)) if values else None


# ── Fills, a page a time ─────────────────────────────────────────────────────────────────────


def fill_from(product: str, r: dict) -> TradeFillModel:
    price, size = dec(r.get("price")), dec(r.get("size"))
    return TradeFillModel(
        product=product,
        symbol=r["symbol"],
        fill_id=str(r["id"]),
        order_id=str(r.get("orderId") or ""),
        time=ms_to_dt(r["timestamp"]),
        side="BUY" if str(r.get("side")).upper() == "BUY" else "SELL",
        price=price,
        qty=size,
        quote_qty=price * size,
        fee=dec(r.get("fee")),
        fee_asset=r.get("feeCoin") or "",
        maker=str(r.get("role")).upper() == "MAKER",
        realized_pnl=None,
        position_side=None,
        raw=r,
    )


async def sync_page(db: AsyncSession, conn: TradeConnectionModel, sync: TradeSyncModel, client: Pionex) -> int:
    """Backwards until the history is in (the cursor is the oldest time seen, the next page ends
    just before it), then forwards from the newest time seen. A short page ends the backfill or the
    catch-up; the symbol is then looked at again in REFRESH."""
    now = utcnow()
    read, page = (client.spot_fills, SPOT_PAGE) if sync.product == "spot" else (client.usdm_fills, USDM_PAGE)
    if not sync.backfilled:
        rows = await read(sync.symbol, None, sync.cursor)
    else:
        rows = await read(sync.symbol, sync.cursor, None)
    fills = [fill_from(sync.product, r) for r in rows]
    new = await store.store_fills(db, conn.id, fills)
    store.note_times(sync, [f.time for f in fills])
    short = len(rows) < page
    if not sync.backfilled:
        if short:
            sync.backfilled = True
            sync.cursor = dt_to_ms(sync.newest_time) if sync.newest_time else dt_to_ms(now)
        else:
            sync.cursor = min(int(r["timestamp"]) for r in rows) - 1
    elif rows:
        sync.cursor = max(int(r["timestamp"]) for r in rows)
    sync.count += new
    sync.last_ok_at = now
    sync.last_error = None
    sync.next_at = now + store.REFRESH if short else now
    return new
