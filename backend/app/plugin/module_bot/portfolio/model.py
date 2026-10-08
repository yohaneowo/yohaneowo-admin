from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class BotPortfolioSnapshotModel(ModelMixin):
    """资产快照：bot 每 15 分钟上报一次交易所资产总额，永久保留"""

    __tablename__: str = "bot_portfolio_snapshot"
    __table_args__ = (
        Index("ix_bot_portfolio_snapshot_created_deleted", "created_time", "is_deleted"),
        {"comment": "Bot资产快照表"},
    )

    total_twd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, comment="总资产(TWD)")
    total_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, comment="总资产(USDT)")
    twd_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, comment="USDT/TWD 汇率")
    accounts: Mapped[list[dict]] = mapped_column(JSON, nullable=False, comment="各账户总额 [{exchange, type, total}]")
    failures: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="读取失败的账户；非空时总额少算了这些账户")
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="快照时间")
