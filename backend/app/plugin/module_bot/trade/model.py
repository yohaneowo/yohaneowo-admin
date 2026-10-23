"""TradeHub's exchange accounts: a website user's read-only API key and what it shows — fills,
balances, futures positions and futures income — read in the background (job.py) for the website's
charts and ledger. Amounts are DECIMAL(36, 18) and Python Decimal end to end; the website only
displays them. Keys are Fernet-encrypted (app/utils/crypto_util.py) and never leave this service."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


Amount = Numeric(36, 18)


class TradeConnectionModel(MappedBase):
    """One exchange account a website user connected with a read-only key. Several per user are
    allowed (a second Binance account, later other exchanges)."""

    __tablename__ = "bot_trade_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_user_id: Mapped[str] = mapped_column(String(64), index=True)
    # binance, bybit, pionex (common.EXCHANGES)
    exchange: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(64))
    api_key: Mapped[str] = mapped_column(Text)
    api_secret: Mapped[str] = mapped_column(Text)
    # The last characters of the key, all the website ever sees of it.
    key_tail: Mapped[str] = mapped_column(String(8))
    # What the exchange says the key may do: reading, trading, withdrawals, futures, ipRestrict.
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    # Symbols the user added by hand (closed positions the balances no longer show):
    # [{"product": "spot", "symbol": "ETHUSDT"}].
    tracked: Mapped[list] = mapped_column(JSON, default=list)
    # ok, error (the key stopped working; shown on the website), disabled
    status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # When the balances, positions and the tracked list are next refreshed.
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TradeSyncModel(MappedBase):
    """Where the fills of one symbol have been read up to. Binance pages forwards from a trade id
    (`fromId`), so the cursor is the next id to ask for; symbol "*" is the futures income stream,
    paged by time. The history of a symbol is read a page a tick, then only new fills are looked for."""

    __tablename__ = "bot_trade_sync"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # spot, coinm (coin-margined contracts), usdm (USDⓈ-margined contracts), stock (US equities)
    product: Mapped[str] = mapped_column(String(8), primary_key=True)
    # The exchange's symbol id (BTCUSDT, BTCUSD_PERP, BTC_USDT), or "*" for a stream over all
    # symbols (Binance's futures income and stock fills, Bybit's fills of a category), paged by time.
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    # balance (an asset held), position (open futures), income (seen in futures income), manual,
    # stock, stream (a whole-category stream)
    source: Mapped[str] = mapped_column(String(16), default="balance")
    # Binance: the next trade id. Time-paged streams: a millisecond timestamp (Pionex symbols page
    # backwards in time until backfilled, then forwards; see flow_pionex.py).
    cursor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Bybit: the page cursor inside the current window.
    page_cursor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    oldest_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    newest_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # True once a page came back short: the whole history is in.
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    next_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TradeFillModel(MappedBase):
    """One fill (a trade of an order; an order can fill in several). Spot: qty in the base asset,
    quote_qty in the quote. COIN-M futures: qty in contracts, quote_qty the coin amount (`baseQty`),
    realized_pnl in the coin. Stocks: qty in shares (fractions allowed), quote_qty in the quote
    (USDC), the fee the order's fee shared out by quantity. Quantities are positive; the side gives
    the direction."""

    __tablename__ = "bot_trade_fill"
    __table_args__ = (Index("ix_bot_trade_fill_time", "connection_id", "product", "symbol", "time"),)

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(8), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    # The exchange's trade id, unique within a symbol: a number for crypto, a UUID for stocks.
    fill_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(40))
    time: Mapped[datetime] = mapped_column(DateTime)
    # BUY / SELL
    side: Mapped[str] = mapped_column(String(4))
    price: Mapped[Decimal] = mapped_column(Amount)
    qty: Mapped[Decimal] = mapped_column(Amount)
    quote_qty: Mapped[Decimal] = mapped_column(Amount)
    fee: Mapped[Decimal] = mapped_column(Amount)
    fee_asset: Mapped[str] = mapped_column(String(16))
    maker: Mapped[bool] = mapped_column(Boolean, default=False)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    # Futures: BOTH (one-way mode), LONG or SHORT (hedge mode).
    position_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeBalanceModel(MappedBase):
    """The latest balance of one asset in one wallet (spot, funding, earn, coinm). Replaced on every
    refresh; the website shows when."""

    __tablename__ = "bot_trade_balance"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset: Mapped[str] = mapped_column(String(16), primary_key=True)
    free: Mapped[Decimal] = mapped_column(Amount)
    locked: Mapped[Decimal] = mapped_column(Amount)
    total: Mapped[Decimal] = mapped_column(Amount)
    # Futures wallets: the unrealized profit in the asset (the total is the wallet balance).
    unrealized: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    # The asset's USD price as the exchange reported it at the refresh (Bybit, Pionex), so coins
    # Binance doesn't list are still valued; the website falls back to Binance's price when null.
    price_usd: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TradePositionModel(MappedBase):
    """An open futures position as the exchange reports it (its entry price and unrealized profit;
    nothing is recomputed here). Replaced on every refresh."""

    __tablename__ = "bot_trade_position"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(8), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    position_side: Mapped[str] = mapped_column(String(8), primary_key=True)
    # Contracts, negative for a short.
    qty: Mapped[Decimal] = mapped_column(Amount)
    entry_price: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    mark_price: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    liquidation_price: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # cross / isolated
    margin_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeBotModel(MappedBase):
    """A trading bot (Pionex: futures grid, hedge grid, spot grid, smart copy) as the exchange
    reports it — its investment, position and profit so far. Running bots are replaced on every
    refresh; finished ones stay as history. Amounts are in the bot's quote (USDT mostly)."""

    __tablename__ = "bot_trade_bot"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # futures_grid, future_hedge_grid, spot_grid, smart_copy
    type: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(64), default="")
    # The symbol the bot trades, in the exchange's format (BTC_USDT_PERP, BTC_USDT).
    symbol: Mapped[str] = mapped_column(String(32))
    base: Mapped[str] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(String(16))
    # running, finished
    status: Mapped[str] = mapped_column(String(16))
    # The bot's own state (running, paused, canceled…) and why it closed.
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # long / short / neutral for futures grids.
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What was put in (quote), the margin balance now, the open position (base units) and its
    # average price.
    investment: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    # Profit so far: from grid arbitrage, all realized (position changes included), fees and
    # funding paid (negative).
    grid_profit: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    funding: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    # Completed grid round trips.
    pairs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    bottom: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    liquidation_price: Mapped[Decimal | None] = mapped_column(Amount, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeIncomeModel(MappedBase):
    """A futures income record: funding fees, realized profit, commissions, transfers in and out of
    the futures wallet. Binance's `tranId` repeats across types, so the type is part of the key."""

    __tablename__ = "bot_trade_income"
    __table_args__ = (Index("ix_bot_trade_income_time", "connection_id", "product", "time"),)

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(8), primary_key=True)
    income_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # FUNDING_FEE, REALIZED_PNL, COMMISSION, TRANSFER, …
    type: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Amount)
    time: Mapped[datetime] = mapped_column(DateTime)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
