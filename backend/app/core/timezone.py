"""显示时区（yohaneowo-admin 新增）。

数据库一律存 UTC（模型默认值用 datetime.now(UTC)，MySQL 也是 UTC），对外则用 DISPLAY_TIMEZONE 的本地时间：
- API 输出的时间：UTC → 本地（validator.DateTimeStr 的序列化、response 的 JSON 编码）；
- 前端送来的时间字串（筛选条件、表单）：本地 → UTC，再拿去查询或写入；
- 按日统计：按本地日期分组（local_date）。
原框架直接把 UTC 当本地显示，所有时间都慢 8 小时，筛选范围也偏了 8 小时。
"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.config.setting import settings
from app.core.logger import logger

FALLBACK_OFFSET = timedelta(hours=8)

try:
    DISPLAY_TZ = ZoneInfo(settings.DISPLAY_TIMEZONE)
except ZoneInfoNotFoundError:
    # 精简的容器映像可能没有时区资料库；台湾 / 中国都是固定 UTC+8、没有夏令时间
    logger.warning(f"找不到时区 {settings.DISPLAY_TIMEZONE}，改用固定的 UTC+8")
    DISPLAY_TZ = timezone(FALLBACK_OFFSET)


def to_display(value: datetime) -> datetime:
    """数据库的时间（不带时区的视为 UTC）→ 本地时间（不带时区）"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(DISPLAY_TZ).replace(tzinfo=None)


def from_display(value: datetime) -> datetime:
    """前端送来的本地时间（不带时区）→ UTC（不带时区，与数据库读出的值同一种形式）"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=DISPLAY_TZ)
    return value.astimezone(UTC).replace(tzinfo=None)


def local_today_start_utc() -> datetime:
    """本地「今天零点」换成 UTC（不带时区），用来跟数据库的时间比较"""
    local_midnight = datetime.now(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC).replace(tzinfo=None)


def _offset_hours() -> float:
    offset = datetime.now(DISPLAY_TZ).utcoffset() or FALLBACK_OFFSET
    return offset.total_seconds() / 3600


def local_date(column: ColumnElement) -> ColumnElement:
    """SQL：时间栏位换成本地日期（按日统计用），依数据库类型产生对应的写法"""
    hours = _offset_hours()
    if settings.DATABASE_TYPE == "mysql":
        sign = "+" if hours >= 0 else "-"
        whole, minutes = divmod(round(abs(hours) * 60), 60)
        return func.date(func.convert_tz(column, "+00:00", f"{sign}{whole:02d}:{minutes:02d}"))
    if settings.DATABASE_TYPE == "postgres":
        return func.date(func.timezone(str(DISPLAY_TZ), func.timezone("UTC", column)))
    return func.date(column, f"{hours:+g} hours")
