from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class AiCostReconcileModel(ModelMixin):
    """费用对账：某个 UTC 日期、某个模型，供应商的实际用量 / 费用 vs AI 调用日志的记录。每次对账整天重写"""

    __tablename__: str = "ai_cost_reconcile"
    __table_args__ = (
        Index("ix_ai_cost_reconcile_day_model", "day", "provider", "model"),
        Index("ix_ai_cost_reconcile_created_deleted", "created_time", "is_deleted"),
        {"comment": "AI费用对账表"},
    )

    day: Mapped[date] = mapped_column(Date, nullable=False, index=True, comment="日期(UTC，与供应商帐单一致)")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="供应商")
    model: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型")
    actual_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="实际输入token")
    actual_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="实际输出token")
    actual_cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="实际快取读取token")
    actual_cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="实际快取写入token")
    actual_cost_usd: Mapped[Decimal] = mapped_column(Numeric(16, 8), nullable=False, default=0, comment="实际费用(美元)")
    logged_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录的调用次数")
    logged_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录的输入token")
    logged_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录的输出token")
    logged_cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录的快取读取token")
    logged_cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录的快取写入token")
    logged_cost_usd: Mapped[Decimal] = mapped_column(Numeric(16, 8), nullable=False, default=0, comment="记录的费用(美元)")
    logged_unpriced_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="记录里未定价的调用数")
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="结果(ok:一致 price_mismatch:单价不对 missing_logs:有调用没记录 extra_logs:记录比实际多)")
    checked_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="对账时间")
