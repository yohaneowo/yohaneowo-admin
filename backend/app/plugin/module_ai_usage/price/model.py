from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class AiModelPriceModel(ModelMixin):
    """AI 模型单价（美元 / 百万 token）。每天从 LiteLLM 公开价格表同步；手动改过的会锁定不再被同步覆盖"""

    __tablename__: str = "ai_model_price"
    __table_args__ = (
        Index("ix_ai_model_price_provider_model", "provider", "model"),
        Index("ix_ai_model_price_created_deleted", "created_time", "is_deleted"),
        {"comment": "AI模型单价表"},
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="供应商(anthropic/voyage/openai/deepseek/gemini)")
    model: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名称(与 API 请求里的 model 一致)")
    input_price: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, comment="输入单价(美元/百万token)")
    output_price: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, comment="输出单价(美元/百万token)")
    cache_read_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True, comment="快取读取单价(美元/百万token)")
    cache_write_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True, comment="快取写入单价(美元/百万token)")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="litellm", comment="来源(litellm:自动同步 manual:手动)")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="锁定后不被自动同步覆盖")
    synced_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近一次从 LiteLLM 同步的时间")
