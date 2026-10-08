from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class BotGroupModel(ModelMixin):
    """Bot 群组表（Discord 服务器 / LINE 群组）

    记录由 bot 上报，不挂 UserMixin：数据权限按 created_id 过滤，bot 写入的记录没有创建人，
    挂了会让非超管角色看不到任何群组。
    """

    __tablename__: str = "bot_group"
    __table_args__ = (
        Index("ix_bot_group_status_deleted", "status", "is_deleted"),
        Index("ix_bot_group_created_deleted", "created_time", "is_deleted"),
        UniqueConstraint("platform", "external_id", name="uq_bot_group_platform_external_id"),
        {"comment": "Bot群组表"},
    )

    platform: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="平台(discord/line)")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="平台侧ID(Discord guild ID / LINE groupId)")
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="群组名称")
    icon_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="头像URL")
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="成员数")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")
    is_joined: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="bot 是否仍在群内")
    joined_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="加入时间")
    left_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="离开时间")
    last_active_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最后活动时间")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
