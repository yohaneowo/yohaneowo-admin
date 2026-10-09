from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class BotVoiceSessionModel(ModelMixin):
    """Discord 语音停留记录：某人在某个语音频道连续待的一段时间一条，离开时由 bot 上报，永久保留"""

    __tablename__: str = "bot_voice_session"
    __table_args__ = (
        Index("ix_bot_voice_session_created_deleted", "created_time", "is_deleted"),
        {"comment": "Bot语音停留记录表"},
    )

    guild_external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="Discord 服务器ID")
    channel_external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="语音频道ID")
    channel_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", comment="语音频道名称(当时)")
    user_external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="用户ID")
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="用户名称(当时)")
    user_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="用户头像URL(当时)")
    joined_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="进入时间")
    joined_time_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="进入时间是否为估计值(bot 启动时人已在频道，用启动时间代替)")
    left_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="离开时间")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, comment="停留秒数")
    end_reason: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="结束原因(leave:离开 move:换频道 restart:bot 重启)")
