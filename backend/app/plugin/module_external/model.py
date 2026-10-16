from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class ExternalApiProviderModel(MappedBase):
    """第三方服务的额度设定：每日上限、按哪个时区换日，以及当天是否已被对方拒绝"""

    __tablename__ = "external_api_provider"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True, comment="服务(tmdb/trakt/omdb/wikipedia…)")
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="每日上限，空 = 不限")
    reset_timezone: Mapped[str] = mapped_column(String(64), default="UTC", comment="额度按这个时区的零点重置")
    note: Mapped[str] = mapped_column(String(255), default="", comment="备注，例如订阅方案")
    blocked_day: Mapped[date | None] = mapped_column(Date, nullable=True, comment="对方拒绝请求（额度用完）的那一天，当天不再调用")
    blocked_reason: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="对方拒绝时的回应")


class ExternalApiUsageModel(MappedBase):
    """第三方服务每天的用量（日期按该服务的 reset_timezone）"""

    __tablename__ = "external_api_usage"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0, comment="实际发出的请求数")
    cache_hits: Mapped[int] = mapped_column(Integer, default=0, comment="命中快取、没有发请求的次数")
    failures: Mapped[int] = mapped_column(Integer, default=0, comment="失败（网络错误、5xx、401、429）")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="UTC")
