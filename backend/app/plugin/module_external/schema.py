from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.core.validator import DateTimeStr


class ExternalUsageTodaySchema(BaseModel):
    requests: int = 0
    cache_hits: int = 0
    failures: int = 0
    last_error: str | None = None
    last_error_at: DateTimeStr | None = None


class ExternalProviderOutSchema(BaseModel):
    provider: str
    label: str
    daily_limit: int | None
    reset_timezone: str
    note: str
    # 需要 key 的服务：key 有没有设（只看有没有值）
    configured: bool
    blocked: bool
    blocked_reason: str | None
    today: ExternalUsageTodaySchema


class ExternalUsageDaySchema(BaseModel):
    day: date
    requests: int
    cache_hits: int
    failures: int


class ExternalProviderSaveSchema(BaseModel):
    """保存设定会解除当天的停止状态（例如刚订阅了付费方案）"""

    daily_limit: int | None = Field(default=None, ge=1)
    reset_timezone: str = Field(default="UTC", max_length=64)
    note: str = Field(default="", max_length=255)

    @field_validator("reset_timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("不认识这个时区") from error
        return value
