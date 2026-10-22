"""The exchange-account logic: connecting a key, refreshing balances and positions, reading fills a
page at a time, and shaping rows for the website. Everything the website gets is a plain dict with
amounts as decimal strings and times in milliseconds."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.crypto_util import CryptoUtil

from .binance import PAGE, Binance, KeyRejected, dec
from .model import TradeBalanceModel, TradeConnectionModel, TradeFillModel, TradeIncomeModel, TradePositionModel, TradeSyncModel

# Balances, positions and the tracked list are refreshed this often; so are symbols whose history is in.
REFRESH = timedelta(minutes=5)
RETRY = timedelta(minutes=10)
# A key that was rejected is tried again this much later (the user may have fixed it).
KEY_RETRY = timedelta(hours=1)
INCOME = "*"
# COIN-M futures opened in 2020; the income history is read from here in windows of 200 days (the
# endpoint's limit).
COINM_EPOCH_MS = 1_577_836_800_000
INCOME_WINDOW_MS = 200 * 24 * 3600 * 1000
PRODUCTS = ("spot", "coinm")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ms_to_dt(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).replace(tzinfo=None)


def dt_to_ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.replace(tzinfo=UTC).timestamp() * 1000)


def num(value: Decimal | None) -> str | None:
    """A decimal as the website shows it: no exponent, no trailing zeros."""
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def client_for(conn: TradeConnectionModel) -> Binance:
    return Binance(CryptoUtil.decrypt(conn.api_key), CryptoUtil.decrypt(conn.api_secret))


# ── Connections ───────────────────────────────────────────────────────────────────────────────


async def connect(db: AsyncSession, site_user_id: str, label: str, api_key: str, api_secret: str) -> TradeConnectionModel:
    """Checks the key with the exchange and stores it encrypted. A key that can withdraw is refused
    (KeyRejected("withdrawals")); one that can't read is useless (KeyRejected("reading"))."""
    client = Binance(api_key, api_secret)
    try:
        permissions = await client.restrictions()
        if permissions["withdrawals"]:
            raise KeyRejected("withdrawals")
        if not permissions["reading"]:
            raise KeyRejected("reading")
        await client.spot_balances()
    finally:
        await client.close()
    row = TradeConnectionModel(
        site_user_id=site_user_id,
        exchange="binance",
        label=label.strip()[:64] or "Binance",
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
    for model in (TradeFillModel, TradeIncomeModel, TradeBalanceModel, TradePositionModel, TradeSyncModel):
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
            markets = await client.markets()
        finally:
            await client.close()
        ids = {m["id"]: m for m in markets.values()}
        for a in add:
            market = ids.get(a["symbol"])
            if market is None or not (market.get("spot") if a["product"] == "spot" else market.get("inverse")):
                raise KeyRejected("unknownSymbol")
            if not any(t["product"] == a["product"] and t["symbol"] == a["symbol"] for t in tracked):
                tracked.append({"product": a["product"], "symbol": a["symbol"]})
        for a in add:
            await _ensure_syncs(db, conn.id, a["product"], [a["symbol"]], "manual", now)
    conn.tracked = tracked


# ── The refresh: balances, positions, which symbols to read ──────────────────────────────────


async def refresh(db: AsyncSession, conn: TradeConnectionModel) -> None:
    """Replaces the balances and positions, and makes sure every symbol worth reading has a sync row."""
    client = client_for(conn)
    try:
        now = utcnow()
        funding = await client.funding_balances()
        earn = await client.earn_balances()
        # Flexible Earn holdings also show in the spot wallet as "LD" + asset: the earn rows have them.
        earned = {b["asset"] for b in earn}
        spot = [b for b in await client.spot_balances() if not (b["asset"].startswith("LD") and b["asset"][2:] in earned)]
        await _replace_balances(db, conn.id, "spot", spot, now)
        await _replace_balances(db, conn.id, "funding", funding, now)
        await _replace_balances(db, conn.id, "earn", earn, now)
        held = {b["asset"] for b in spot + funding + earn}
        await _ensure_syncs(db, conn.id, "spot", await client.spot_symbols_for(held), "balance", now)
        # Reading the futures wallet needs only "Enable Reading" (the key's futures permission is
        # for trading), so it's tried on every account; one without futures says no once, and is
        # asked again when the user refreshes by hand.
        if not conn.permissions.get("coinmUnavailable"):
            try:
                wallet = await client.coinm_wallet()
                positions = await client.coinm_positions()
            except KeyRejected:
                conn.permissions = {**conn.permissions, "coinmUnavailable": True}
            else:
                await _replace_balances(db, conn.id, "coinm", wallet, now)
                await _replace_positions(db, conn.id, "coinm", positions, now)
                await _ensure_syncs(db, conn.id, "coinm", [INCOME], "income", now)
                await _ensure_syncs(db, conn.id, "coinm", sorted({p["symbol"] for p in positions}), "position", now)
                seen = await db.scalars(
                    select(TradeIncomeModel.symbol).where(TradeIncomeModel.connection_id == conn.id, TradeIncomeModel.product == "coinm", TradeIncomeModel.symbol.is_not(None)).distinct()
                )
                await _ensure_syncs(db, conn.id, "coinm", sorted(set(seen)), "income", now)
        for t in conn.tracked:
            await _ensure_syncs(db, conn.id, t["product"], [t["symbol"]], "manual", now)
        conn.status = "ok"
        conn.last_error = None
        conn.last_sync_at = now
        conn.next_check_at = now + REFRESH
    finally:
        await client.close()


async def _replace_balances(db: AsyncSession, connection_id: int, wallet: str, rows: list[dict], now: datetime) -> None:
    await db.execute(delete(TradeBalanceModel).where(TradeBalanceModel.connection_id == connection_id, TradeBalanceModel.wallet == wallet))
    for b in rows:
        total = b.get("total", b["free"] + b["locked"])
        db.add(TradeBalanceModel(connection_id=connection_id, wallet=wallet, asset=b["asset"], free=b["free"], locked=b["locked"], total=total, unrealized=b.get("unrealized"), updated_at=now))


async def _replace_positions(db: AsyncSession, connection_id: int, product: str, rows: list[dict], now: datetime) -> None:
    await db.execute(delete(TradePositionModel).where(TradePositionModel.connection_id == connection_id, TradePositionModel.product == product))
    for p in rows:
        db.add(
            TradePositionModel(
                connection_id=connection_id,
                product=product,
                symbol=p["symbol"],
                position_side=p.get("positionSide") or "BOTH",
                qty=dec(p["positionAmt"]),
                entry_price=dec(p.get("entryPrice")) or None,
                mark_price=dec(p.get("markPrice")) or None,
                unrealized_pnl=dec(p.get("unRealizedProfit")),
                liquidation_price=dec(p.get("liquidationPrice")) or None,
                leverage=int(p["leverage"]) if p.get("leverage") else None,
                margin_type=p.get("marginType"),
                updated_at=now,
                raw=p,
            )
        )


async def _ensure_syncs(db: AsyncSession, connection_id: int, product: str, symbols: list[str], source: str, now: datetime) -> None:
    if not symbols:
        return
    have = set(await db.scalars(select(TradeSyncModel.symbol).where(TradeSyncModel.connection_id == connection_id, TradeSyncModel.product == product, TradeSyncModel.symbol.in_(symbols))))
    for symbol in symbols:
        if symbol not in have:
            db.add(TradeSyncModel(connection_id=connection_id, product=product, symbol=symbol, source=source, next_at=now))
    await db.flush()


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
        fill_id=int(r["id"]),
        order_id=int(r["orderId"]),
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


async def sync_page(db: AsyncSession, conn: TradeConnectionModel, sync: TradeSyncModel, client: Binance) -> int:
    """Reads one page for a sync row and moves its cursor; returns how many new rows were stored.
    A short page means the history is in: the next look is in REFRESH, else right away."""
    now = utcnow()
    if sync.symbol == INCOME:
        start = sync.cursor or COINM_EPOCH_MS
        now_ms = dt_to_ms(now)
        # Up to ten empty windows a page: an account that opened futures recently has years of nothing.
        for _ in range(10):
            end = min(start + INCOME_WINDOW_MS, now_ms)
            rows = await client.coinm_income(start, end)
            if rows or end >= now_ms:
                break
            start = end
        new = await _store_income(db, conn.id, sync.product, rows)
        if rows:
            times = [ms_to_dt(r["time"]) for r in rows]
            sync.oldest_time = min([sync.oldest_time, *times]) if sync.oldest_time else min(times)
            sync.newest_time = max([sync.newest_time, *times]) if sync.newest_time else max(times)
        # A full page: continue from its last moment (that moment again, deduplicated); else the
        # window is done.
        sync.cursor = max(int(r["time"]) for r in rows) if len(rows) >= PAGE else end
        short = len(rows) < PAGE and end >= now_ms
    else:
        read = client.spot_trades if sync.product == "spot" else client.coinm_trades
        rows = await read(sync.symbol, sync.cursor or 0)
        fills = [fill_from(sync.product, sync.symbol, r) for r in rows]
        new = await _store_fills(db, conn.id, fills)
        if rows:
            sync.cursor = max(int(r["id"]) for r in rows) + 1
            times = [f.time for f in fills]
            sync.oldest_time = min([sync.oldest_time, *times]) if sync.oldest_time else min(times)
            sync.newest_time = max([sync.newest_time, *times]) if sync.newest_time else max(times)
        short = len(rows) < PAGE
    sync.count += new
    sync.last_ok_at = now
    sync.last_error = None
    sync.backfilled = sync.backfilled or short
    sync.next_at = now + REFRESH if short else now
    return new


async def _store_fills(db: AsyncSession, connection_id: int, fills: list[TradeFillModel]) -> int:
    if not fills:
        return 0
    product, symbol = fills[0].product, fills[0].symbol
    have = set(
        await db.scalars(
            select(TradeFillModel.fill_id).where(
                TradeFillModel.connection_id == connection_id, TradeFillModel.product == product, TradeFillModel.symbol == symbol, TradeFillModel.fill_id.in_([f.fill_id for f in fills])
            )
        )
    )
    new = 0
    for f in fills:
        if f.fill_id not in have:
            f.connection_id = connection_id
            db.add(f)
            have.add(f.fill_id)
            new += 1
    await db.flush()
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
        {"wallet": b.wallet, "asset": b.asset, "free": num(b.free), "locked": num(b.locked), "total": num(b.total), "unrealized": num(b.unrealized), "updatedAt": dt_to_ms(b.updated_at)} for b in rows
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
            "leverage": p.leverage,
            "marginType": p.margin_type,
            "updatedAt": dt_to_ms(p.updated_at),
        }
        for p in rows
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
    """What the spot fills add up to per asset against what the spot, funding and earn wallets hold.
    A difference is a deposit, withdrawal, transfer, earn reward or an untracked symbol — the
    website shows it, nothing is adjusted."""
    computed: dict[str, Decimal] = {}

    def bump(asset: str, amount: Decimal) -> None:
        if asset:
            computed[asset] = computed.get(asset, Decimal(0)) + amount

    rows = await db.scalars(select(TradeFillModel).where(TradeFillModel.connection_id == connection_id, TradeFillModel.product == "spot"))
    for f in rows:
        base, quote = _split_spot(f.symbol)
        sign = 1 if f.side == "BUY" else -1
        bump(base, sign * f.qty)
        bump(quote, -sign * f.quote_qty)
        bump(f.fee_asset, -f.fee)
    actual: dict[str, Decimal] = {}
    for b in await db.scalars(select(TradeBalanceModel).where(TradeBalanceModel.connection_id == connection_id, TradeBalanceModel.wallet.in_(("spot", "funding", "earn")))):
        actual[b.asset] = actual.get(b.asset, Decimal(0)) + b.total
    out = []
    for asset in sorted(set(computed) | set(actual)):
        c, a = computed.get(asset, Decimal(0)), actual.get(asset, Decimal(0))
        if c or a:
            out.append({"asset": asset, "computed": num(c), "actual": num(a), "diff": num(a - c)})
    return out


def _split_spot(symbol: str) -> tuple[str, str]:
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "DAI"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""
