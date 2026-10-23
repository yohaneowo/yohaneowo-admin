"""The exchange-account logic shared by every exchange: connecting a key, which flow reads which
connection (flow_binance.py, flow_bybit.py, flow_pionex.py), and shaping rows for the website.
Everything the website gets is a plain dict with amounts as decimal strings and times in milliseconds."""

from datetime import datetime
from decimal import Decimal
from types import ModuleType

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.crypto_util import CryptoUtil

from . import flow_binance, flow_bybit, flow_pionex, store
from .binance import Binance
from .bybit import Bybit
from .common import EXCHANGES, KeyRejected, dec, dt_to_ms, num, utcnow
from .model import TradeBalanceModel, TradeBotModel, TradeConnectionModel, TradeFillModel, TradeIncomeModel, TradePositionModel, TradeSyncModel
from .pionex import Pionex
from .store import KEY_RETRY, REFRESH, RETRY

__all__ = ["EXCHANGES", "KEY_RETRY", "REFRESH", "RETRY", "utcnow"]

FLOWS: dict[str, ModuleType] = {"binance": flow_binance, "bybit": flow_bybit, "pionex": flow_pionex}
CLIENTS = {"binance": Binance, "bybit": Bybit, "pionex": Pionex}
TITLES = {"binance": "Binance", "bybit": "Bybit", "pionex": "Pionex"}

Client = Binance | Bybit | Pionex


def make_client(exchange: str, api_key: str, api_secret: str) -> Client:
    return CLIENTS[exchange](api_key, api_secret)


def client_for(conn: TradeConnectionModel) -> Client:
    return make_client(conn.exchange, CryptoUtil.decrypt(conn.api_key), CryptoUtil.decrypt(conn.api_secret))


# ── Connections ───────────────────────────────────────────────────────────────────────────────


async def connect(db: AsyncSession, site_user_id: str, exchange: str, label: str, api_key: str, api_secret: str) -> TradeConnectionModel:
    """Checks the key with the exchange and stores it encrypted. A key that can withdraw is refused
    (KeyRejected("withdrawals")); one that can't read is useless (KeyRejected("reading")); a Bybit
    account that isn't unified can't be read (KeyRejected("classic"))."""
    client = make_client(exchange, api_key, api_secret)
    try:
        permissions = await FLOWS[exchange].check(client)
    finally:
        await client.close()
    row = TradeConnectionModel(
        site_user_id=site_user_id,
        exchange=exchange,
        label=label.strip()[:64] or TITLES[exchange],
        api_key=CryptoUtil.encrypt(api_key),
        api_secret=CryptoUtil.encrypt(api_secret),
        key_tail=api_key[-4:],
        permissions=permissions,
        tracked=[],
        status="ok",
        next_check_at=utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


async def connection(db: AsyncSession, site_user_id: str, connection_id: int) -> TradeConnectionModel | None:
    row = await db.get(TradeConnectionModel, connection_id)
    return row if row is not None and row.site_user_id == site_user_id else None


async def connections(db: AsyncSession, site_user_id: str) -> list[TradeConnectionModel]:
    return list(await db.scalars(select(TradeConnectionModel).where(TradeConnectionModel.site_user_id == site_user_id).order_by(TradeConnectionModel.id)))


async def disconnect(db: AsyncSession, conn: TradeConnectionModel) -> None:
    """Forgets the key and everything read with it."""
    for model in (TradeFillModel, TradeIncomeModel, TradeBalanceModel, TradePositionModel, TradeBotModel, TradeSyncModel):
        await db.execute(delete(model).where(model.connection_id == conn.id))
    await db.delete(conn)


def key_failed(conn: TradeConnectionModel, error: KeyRejected, now: datetime) -> None:
    """The exchange rejected the key: stop reading until it's tried again later."""
    conn.status = "error"
    conn.last_error = str(error)[:500]
    conn.next_check_at = now + KEY_RETRY


async def set_tracked(db: AsyncSession, conn: TradeConnectionModel, add: list[dict], remove: list[dict]) -> None:
    """Symbols the user follows by hand. Added ones are checked against the exchange's markets
    (KeyRejected("unknownSymbol") otherwise); removed ones lose their fills too."""
    now = utcnow()
    tracked = [t for t in conn.tracked if not any(t["product"] == r["product"] and t["symbol"] == r["symbol"] for r in remove)]
    for r in remove:
        await db.execute(delete(TradeSyncModel).where(TradeSyncModel.connection_id == conn.id, TradeSyncModel.product == r["product"], TradeSyncModel.symbol == r["symbol"]))
        await db.execute(delete(TradeFillModel).where(TradeFillModel.connection_id == conn.id, TradeFillModel.product == r["product"], TradeFillModel.symbol == r["symbol"]))
    if add:
        client = client_for(conn)
        try:
            for a in add:
                if not await FLOWS[conn.exchange].validate_tracked(client, a["product"], a["symbol"]):
                    raise KeyRejected("unknownSymbol")
                if not any(t["product"] == a["product"] and t["symbol"] == a["symbol"] for t in tracked):
                    tracked.append({"product": a["product"], "symbol": a["symbol"]})
        finally:
            await client.close()
        for a in add:
            await store.ensure_syncs(db, conn.id, a["product"], [a["symbol"]], "manual", now)
    conn.tracked = tracked


# ── The refresh and the pages, dispatched to the exchange's flow ─────────────────────────────


async def refresh(db: AsyncSession, conn: TradeConnectionModel) -> None:
    """Replaces the balances, positions and bots, and makes sure every symbol worth reading has a
    sync row; the symbols the user follows by hand on top."""
    client = client_for(conn)
    try:
        now = utcnow()
        await FLOWS[conn.exchange].refresh(db, conn, client, now)
        for t in conn.tracked:
            await store.ensure_syncs(db, conn.id, t["product"], [t["symbol"]], "manual", now)
        conn.status = "ok"
        conn.last_error = None
        conn.last_sync_at = now
        conn.next_check_at = now + REFRESH
    finally:
        await client.close()


async def sync_page(db: AsyncSession, conn: TradeConnectionModel, sync: TradeSyncModel, client: Client) -> int:
    """Reads one page for a sync row and moves its cursor; returns how many new rows were stored."""
    return await FLOWS[conn.exchange].sync_page(db, conn, sync, client)


# ── What the website reads ───────────────────────────────────────────────────────────────────


async def syncs(db: AsyncSession, connection_id: int) -> list[TradeSyncModel]:
    return list(await db.scalars(select(TradeSyncModel).where(TradeSyncModel.connection_id == connection_id).order_by(TradeSyncModel.product, TradeSyncModel.symbol)))


def connection_out(conn: TradeConnectionModel, rows: list[TradeSyncModel]) -> dict:
    return {
        "id": conn.id,
        "exchange": conn.exchange,
        "label": conn.label,
        "keyTail": conn.key_tail,
        "permissions": conn.permissions,
        "tracked": conn.tracked,
        "status": conn.status,
        "lastError": conn.last_error,
        "createdAt": dt_to_ms(conn.created_at),
        "lastSyncAt": dt_to_ms(conn.last_sync_at),
        "syncs": [
            {
                "product": s.product,
                "symbol": s.symbol,
                "source": s.source,
                "count": s.count,
                "backfilled": s.backfilled,
                "oldestTime": dt_to_ms(s.oldest_time),
                "newestTime": dt_to_ms(s.newest_time),
                "lastOkAt": dt_to_ms(s.last_ok_at),
                "lastError": s.last_error,
            }
            for s in rows
        ],
    }


async def balances(db: AsyncSession, connection_id: int) -> list[dict]:
    rows = await db.scalars(select(TradeBalanceModel).where(TradeBalanceModel.connection_id == connection_id).order_by(TradeBalanceModel.wallet, TradeBalanceModel.asset))
    return [
        {
            "wallet": b.wallet,
            "asset": b.asset,
            "free": num(b.free),
            "locked": num(b.locked),
            "total": num(b.total),
            "unrealized": num(b.unrealized),
            "priceUsd": num(b.price_usd),
            "updatedAt": dt_to_ms(b.updated_at),
        }
        for b in rows
    ]


async def positions(db: AsyncSession, connection_id: int) -> list[dict]:
    rows = await db.scalars(select(TradePositionModel).where(TradePositionModel.connection_id == connection_id).order_by(TradePositionModel.product, TradePositionModel.symbol))
    return [
        {
            "product": p.product,
            "symbol": p.symbol,
            "positionSide": p.position_side,
            "qty": num(p.qty),
            "entryPrice": num(p.entry_price),
            "markPrice": num(p.mark_price),
            "unrealizedPnl": num(p.unrealized_pnl),
            "liquidationPrice": num(p.liquidation_price),
            # The price the position breaks even at, fees included (Binance reports it).
            "breakEvenPrice": num(dec(p.raw.get("breakEvenPrice"))) if p.raw.get("breakEvenPrice") else None,
            "leverage": p.leverage,
            "marginType": p.margin_type,
            "updatedAt": dt_to_ms(p.updated_at),
        }
        for p in rows
    ]


async def bots(db: AsyncSession, connection_id: int) -> list[dict]:
    """Running bots first, then finished ones newest first."""
    rows = list(await db.scalars(select(TradeBotModel).where(TradeBotModel.connection_id == connection_id)))
    rows.sort(key=lambda b: (b.status != "running", -(dt_to_ms(b.closed_at or b.created_at) or 0)))
    return [
        {
            "id": b.bot_id,
            "type": b.type,
            "name": b.name,
            "symbol": b.symbol,
            "base": b.base,
            "quote": b.quote,
            "status": b.status,
            "state": b.state,
            "reason": b.raw.get("reasonBy") or None,
            "side": b.side,
            "leverage": b.leverage,
            "investment": num(b.investment),
            "margin": num(b.margin),
            "qty": num(b.qty),
            "entryPrice": num(b.entry_price),
            "gridProfit": num(b.grid_profit),
            "realizedPnl": num(b.realized_pnl),
            "fee": num(b.fee),
            "funding": num(b.funding),
            "pairs": b.pairs,
            "top": num(b.top),
            "bottom": num(b.bottom),
            "rows": b.rows,
            # arithmetic / geometric: how the grid levels are spaced (the website draws them).
            "gridType": (b.raw.get("data") or {}).get("gridType") or None,
            "liquidationPrice": num(b.liquidation_price),
            "createdAt": dt_to_ms(b.created_at),
            "closedAt": dt_to_ms(b.closed_at),
            "updatedAt": dt_to_ms(b.updated_at),
        }
        for b in rows
    ]


def fill_out(f: TradeFillModel) -> dict:
    return {
        "id": f.fill_id,
        "orderId": f.order_id,
        "product": f.product,
        "symbol": f.symbol,
        "time": dt_to_ms(f.time),
        "side": f.side,
        "price": num(f.price),
        "qty": num(f.qty),
        "quoteQty": num(f.quote_qty),
        "fee": num(f.fee),
        "feeAsset": f.fee_asset,
        "maker": f.maker,
        "realizedPnl": num(f.realized_pnl),
        "positionSide": f.position_side,
    }


async def fills(db: AsyncSession, connection_id: int, product: str | None, symbol: str | None, since: datetime | None, until: datetime | None, limit: int) -> list[TradeFillModel]:
    """Fills in a time range, oldest first (the newest `limit` of them when there are more)."""
    query = select(TradeFillModel).where(TradeFillModel.connection_id == connection_id)
    if product:
        query = query.where(TradeFillModel.product == product)
    if symbol:
        query = query.where(TradeFillModel.symbol == symbol)
    if since:
        query = query.where(TradeFillModel.time >= since)
    if until:
        query = query.where(TradeFillModel.time <= until)
    rows = list(await db.scalars(query.order_by(TradeFillModel.time.desc(), TradeFillModel.fill_id.desc()).limit(limit)))
    rows.reverse()
    return rows


async def income(db: AsyncSession, connection_id: int, since: datetime | None, until: datetime | None, limit: int) -> list[dict]:
    query = select(TradeIncomeModel).where(TradeIncomeModel.connection_id == connection_id)
    if since:
        query = query.where(TradeIncomeModel.time >= since)
    if until:
        query = query.where(TradeIncomeModel.time <= until)
    rows = list(await db.scalars(query.order_by(TradeIncomeModel.time.desc(), TradeIncomeModel.income_id.desc()).limit(limit)))
    rows.reverse()
    return [{"id": r.income_id, "product": r.product, "type": r.type, "symbol": r.symbol, "asset": r.asset, "amount": num(r.amount), "time": dt_to_ms(r.time)} for r in rows]


async def reconcile(db: AsyncSession, connection_id: int) -> list[dict]:
    """What the spot and stock fills add up to per asset against what the spot-like wallets hold
    (stocks are held as EQ_<ticker>). A difference is a deposit, withdrawal, transfer, earn reward or
    an untracked symbol — the website shows it, nothing is adjusted."""
    computed: dict[str, Decimal] = {}

    def bump(asset: str, amount: Decimal) -> None:
        if asset:
            computed[asset] = computed.get(asset, Decimal(0)) + amount

    rows = await db.scalars(select(TradeFillModel).where(TradeFillModel.connection_id == connection_id, TradeFillModel.product.in_(("spot", "stock"))))
    for f in rows:
        base, quote = (f"EQ_{f.symbol}", f.raw.get("quote") or "USDC") if f.product == "stock" else split_spot(f.symbol)
        sign = 1 if f.side == "BUY" else -1
        bump(base, sign * f.qty)
        bump(quote, -sign * f.quote_qty)
        bump(f.fee_asset, -f.fee)
    actual: dict[str, Decimal] = {}
    for b in await db.scalars(select(TradeBalanceModel).where(TradeBalanceModel.connection_id == connection_id, TradeBalanceModel.wallet.in_(("spot", "funding", "earn", "unified", "bot")))):
        actual[b.asset] = actual.get(b.asset, Decimal(0)) + b.total
    out = []
    for asset in sorted(set(computed) | set(actual)):
        c, a = computed.get(asset, Decimal(0)), actual.get(asset, Decimal(0))
        if c or a:
            out.append({"asset": asset, "computed": num(c), "actual": num(a), "diff": num(a - c)})
    return out


def split_spot(symbol: str) -> tuple[str, str]:
    """BTCUSDT → (BTC, USDT); Pionex's BTC_USDT → (BTC, USDT)."""
    if "_" in symbol:
        base, _, quote = symbol.partition("_")
        return base, quote
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "DAI"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""
