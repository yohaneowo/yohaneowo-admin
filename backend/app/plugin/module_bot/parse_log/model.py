from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class BotParseLogModel(ModelMixin):
    """Bot 解析日志：每次解析一个链接一条，永久保留"""

    __tablename__: str = "bot_parse_log"
    __table_args__ = (
        Index("ix_bot_parse_log_created_deleted", "created_time", "is_deleted"),
        {"comment": "Bot解析日志表"},
    )

    platform: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="平台(discord/line)")
    source: Mapped[str] = mapped_column(String(16), nullable=False, comment="触发方式(auto:自动解析 command:指令)")
    group_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="群组平台侧ID，私聊为空")
    user_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="用户平台侧ID")
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="用户名称(当时)")
    url: Mapped[str] = mapped_column(String(2048), nullable=False, comment="链接")
    site: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="网站")
    result: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="结果(success/failed/busy)")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误原因")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时(毫秒)")
    file_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="文件数")
    compressed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否压缩过")
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="发生时间")
