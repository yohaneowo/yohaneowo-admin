"""TradeHub: Binance rows become fills, a page moves the cursor, the ledger reconciles with the wallets."""

import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.plugin.module_bot.trade import service
from app.plugin.module_bot.trade.model import TradeBalanceModel, TradeConnectionModel, TradeFillModel, TradeIncomeModel, TradeSyncModel

MODELS = (TradeConnectionModel, TradeSyncModel, TradeFillModel, TradeBalanceModel, TradeIncomeModel)


def _spot(i, qty, price, buyer=True, fee="0.001", fee_asset="BNB"):
    return {"id": i, "orderId": 100 + i, "price": price, "qty": qty, "quoteQty": str(Decimal(qty) * Decimal(price)), "commission": fee, "commissionAsset": fee_asset, "time": 1_700_000_000_000 + i * 1000, "isBuyer": buyer, "isMaker": True}


def _coinm(i, side, qty, price, pnl="0"):
    return {"symbol": "BTCUSD_PERP", "id": i, "orderId": 500 + i, "pair": "BTCUSD", "side": side, "price": price, "qty": qty, "realizedPnl": pnl, "marginAsset": "BTC", "baseQty": "0.01", "commission": "0.00001", "commissionAsset": "BTC", "time": 1_700_000_000_000 + i, "positionSide": "BOTH", "buyer": side == "BUY", "maker": False}


class FakeClient:
    def __init__(self, pages):
        self.pages = pages  # (product, symbol) -> list of pages
        self.asked = []

    async def spot_trades(self, symbol, from_id):
        self.asked.append(("spot", symbol, from_id))
        return self.pages[("spot", symbol)].pop(0)

    async def coinm_trades(self, symbol, from_id):
        self.asked.append(("coinm", symbol, from_id))
        return self.pages[("coinm", symbol)].pop(0)

    async def coinm_income(self, start, end):
        self.asked.append(("income", start, end))
        return self.pages[("coinm", "*")].pop(0)


def test_a_spot_trade_and_a_futures_trade_become_fills():
    spot = service.fill_from("spot", "BTCUSDT", _spot(7, "0.5", "60000", buyer=False))
    assert (spot.side, spot.qty, spot.quote_qty, spot.fee_asset, spot.realized_pnl) == ("SELL", Decimal("0.5"), Decimal("30000"), "BNB", None)
    assert spot.time == datetime(2023, 11, 14, 22, 13, 27)
    fut = service.fill_from("coinm", "BTCUSD_PERP", _coinm(3, "SELL", "12", "61000.5", pnl="0.00042"))
    assert (fut.side, fut.qty, fut.quote_qty, fut.realized_pnl, fut.position_side) == ("SELL", Decimal(12), Decimal("0.01"), Decimal("0.00042"), "BOTH")


def test_amounts_are_shown_without_exponents_or_trailing_zeros():
    assert service.num(Decimal("0.000000000000000000")) == "0"
    assert service.num(Decimal("12.500000000000000000")) == "12.5"
    assert service.num(Decimal("1E+2")) == "100"
    assert service.num(None) is None


def test_pages_move_the_cursor_and_a_short_page_ends_the_backfill(monkeypatch):
    now = datetime(2026, 10, 22, 12, 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(service, "PAGE", 3)
    full = [_spot(1, "1", "100"), _spot(2, "1", "110"), _spot(3, "1", "120")]
    # The second page repeats the last fill of the first (Binance pages are inclusive of fromId).
    short = [_spot(3, "1", "120"), _spot(4, "0.5", "130", buyer=False)]
    income_full = [{"tranId": i, "incomeType": "FUNDING_FEE", "symbol": "BTCUSD_PERP", "asset": "BTC", "income": "-0.00001", "time": 1_700_000_000_000 + i} for i in range(3)]
    income_short = [{"tranId": 2, "incomeType": "FUNDING_FEE", "symbol": "BTCUSD_PERP", "asset": "BTC", "income": "-0.00001", "time": 1_700_000_000_002}, {"tranId": 2, "incomeType": "COMMISSION", "symbol": "BTCUSD_PERP", "asset": "BTC", "income": "-0.00002", "time": 1_700_000_000_002}]
    client = FakeClient({("spot", "BTCUSDT"): [full, short], ("coinm", "*"): [income_full, income_short, []]})

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for model in MODELS:
                await conn.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            conn = TradeConnectionModel(id=1, site_user_id="u", exchange="binance", label="b", api_key="", api_secret="", key_tail="abcd", permissions={}, tracked=[])
            db.add(conn)
            spot = TradeSyncModel(connection_id=1, product="spot", symbol="BTCUSDT", source="balance", next_at=now)
            income = TradeSyncModel(connection_id=1, product="coinm", symbol="*", source="income", next_at=now)
            db.add_all([spot, income])
            await db.flush()
            steps = [await service.sync_page(db, conn, spot, client), await service.sync_page(db, conn, spot, client)]
            income_steps = [await service.sync_page(db, conn, income, client), await service.sync_page(db, conn, income, client)]
            fills = list(await db.scalars(select(TradeFillModel).order_by(TradeFillModel.fill_id)))
            incomes = list(await db.scalars(select(TradeIncomeModel)))
            return steps, spot, income_steps, income, fills, incomes, client.asked

    steps, spot, income_steps, income, fills, incomes, asked = asyncio.run(run())
    assert steps == [3, 1] and [f.fill_id for f in fills] == [1, 2, 3, 4]
    assert spot.cursor == 5 and spot.count == 4 and spot.backfilled is True
    assert spot.next_at == now + service.REFRESH
    assert asked[:2] == [("spot", "BTCUSDT", 0), ("spot", "BTCUSDT", 4)]
    # A full page of income continues from its last moment; the same tranId with another type is kept.
    assert income_steps == [3, 1] and len(incomes) == 4
    assert asked[2][1] == service.COINM_EPOCH_MS and asked[3][1] == 1_700_000_000_002
    # The window ends well before now, so the backfill goes on at once.
    assert income.backfilled is False and income.next_at == now


def test_the_ledger_reconciles_against_the_wallets():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for model in MODELS:
                await conn.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            for r in (_spot(1, "2", "100"), _spot(2, "0.5", "200", buyer=False, fee="0.1", fee_asset="USDT")):
                fill = service.fill_from("spot", "ETHUSDT", r)
                fill.connection_id = 1
                db.add(fill)
            db.add(TradeBalanceModel(connection_id=1, wallet="spot", asset="ETH", free=Decimal("1"), locked=Decimal(0), total=Decimal("1")))
            db.add(TradeBalanceModel(connection_id=1, wallet="earn", asset="ETH", free=Decimal(0), locked=Decimal("0.5"), total=Decimal("0.5")))
            db.add(TradeBalanceModel(connection_id=1, wallet="spot", asset="USDT", free=Decimal("50"), locked=Decimal(0), total=Decimal("50")))
            await db.flush()
            return await service.reconcile(db, 1)

    rows = {r["asset"]: r for r in asyncio.run(run())}
    # Bought 2 ETH, sold 0.5: 1.5 held across spot and earn. USDT: -200 + 100 - 0.1 fee = -100.1
    # from fills, 50 in the wallet, so 150.1 came from elsewhere. The BNB fee shows as owed.
    assert rows["ETH"] == {"asset": "ETH", "computed": "1.5", "actual": "1.5", "diff": "0"}
    assert rows["USDT"] == {"asset": "USDT", "computed": "-100.1", "actual": "50", "diff": "150.1"}
    assert rows["BNB"]["computed"] == "-0.001"
