from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class AiCallLogModel(ModelMixin):
    """AI 调用记录：任何项目每调用一次 AI（对话、向量……）一条，永久保留。
    费用在写入时按当下的单价算好存下来，之后调价不影响历史；没有单价的先留空（未定价），价格表补上后再补算。"""

    __tablename__: str = "ai_call_log"
    __table_args__ = (
        Index("ix_ai_call_log_created_deleted", "created_time", "is_deleted"),
        Index("ix_ai_call_log_user", "user_platform", "user_external_id"),
        {"comment": "AI调用记录表"},
    )

    project: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="调用的项目(如 yohaneowo-bot)")
    feature: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="功能(如 movie-judge)")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="供应商(anthropic/voyage/...)")
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="模型")
    trigger: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="触发方式(daily:定时 command:指令 ...)")
    user_platform: Mapped[str] = mapped_column(String(16), nullable=False, comment="费用归属的平台(discord/line/system...)，共用的调用记在 system")
    user_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="费用归属的用户平台侧ID，system 为空")
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="用户名称(当时)")
    user_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="用户头像URL(当时)")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="输入token")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="输出token")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="快取读取token")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="快取写入token")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(16, 8), nullable=True, comment="费用(美元)，空=未定价")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时(毫秒)")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, comment="是否成功")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="调用时间")
