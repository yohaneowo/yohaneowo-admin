from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class BotRuntimeLogModel(ModelMixin):
    """Bot 运行日志：bot 进程的 console 输出，永久保留"""

    __tablename__: str = "bot_runtime_log"
    __table_args__ = (
        Index("ix_bot_runtime_log_created_deleted", "created_time", "is_deleted"),
        {"comment": "Bot运行日志表"},
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="来源进程(discord/line)")
    level: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="等级(info/warn/error)")
    site: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="正在解析的网站，与解析无关的日志为空")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="发生时间")
