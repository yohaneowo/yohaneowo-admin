"""第三方接口的用量与额度。

每个服务每天记一行：实际请求、命中快取、失败。额度在「接口管理 → 第三方接口」页面调整，不写死在代码里：
订阅升级后只要改上限（或清空 = 不限）。用到上限的 90% 记 warning，用完记 error；
对方自己回「额度用完」时调用 block()，当天不再请求，保存设定或换日后自动恢复。
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from .model import ExternalApiProviderModel, ExternalApiUsageModel

# 新服务第一次被调用时按这里建立设定；之后以数据库（页面上的修改）为准
PROVIDERS: dict[str, dict] = {
    "tmdb": {"label": "TMDB", "env": "TMDB_API_TOKEN", "daily_limit": None, "note": ""},
    "trakt": {"label": "Trakt", "env": "TRAKT_CLIENT_ID", "daily_limit": None, "note": ""},
    "omdb": {"label": "OMDb", "env": "OMDB_API_KEY", "daily_limit": 1000, "note": "免费方案每天 1000 次"},
    "wikipedia": {"label": "Wikipedia", "env": None, "daily_limit": None, "note": "不需要 key，请求间隔 0.4 秒"},
    "anilist": {"label": "AniList", "env": None, "daily_limit": None, "note": "不需要 key，每分钟约 30 次（请求间隔 2.1 秒）"},
    "bangumi": {"label": "Bangumi", "env": None, "daily_limit": None, "note": "不需要 key，请求间隔 0.5 秒"},
    "mal": {"label": "MyAnimeList", "env": "MAL_CLIENT_ID", "daily_limit": None, "note": "有 MAL_CLIENT_ID 用官方 API，没有就走 Jikan"},
    "stratz": {"label": "STRATZ", "env": "STRATZ_API_TOKEN", "daily_limit": None, "note": "DotaHub 的死亡地图：每 5 分钟查一次已连结帐号的天梯比赛，补历史时一页 100 场"},
    "opendota": {"label": "OpenDota", "env": None, "daily_limit": 800, "note": "不需要 key；DotaHub 段位样本按牌位找公开比赛。免费额度每天约 2000 次，跟网站共用同一个 IP"},
}
WARN_SHARE = 0.9


def label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider)


def local_day(timezone: str) -> date:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return datetime.now(zone).date()


async def settings(db: AsyncSession, provider: str) -> ExternalApiProviderModel:
    row = await db.get(ExternalApiProviderModel, provider)
    if row is None:
        defaults = PROVIDERS.get(provider, {})
        row = ExternalApiProviderModel(
            provider=provider,
            daily_limit=defaults.get("daily_limit"),
            reset_timezone="UTC",
            note=defaults.get("note", ""),
        )
        db.add(row)
        await db.flush()
    return row


async def used_today(db: AsyncSession, row: ExternalApiProviderModel) -> int:
    day = local_day(row.reset_timezone)
    query = select(ExternalApiUsageModel.requests).where(ExternalApiUsageModel.provider == row.provider, ExternalApiUsageModel.day == day)
    return await db.scalar(query) or 0


async def remaining(db: AsyncSession, provider: str) -> int | None:
    """今天还能发几个请求；None = 不限。被对方拒绝的当天是 0。"""
    row = await settings(db, provider)
    if row.blocked_day == local_day(row.reset_timezone):
        return 0
    if row.daily_limit is None:
        return None
    return max(0, row.daily_limit - await used_today(db, row))


async def record(
    db: AsyncSession,
    provider: str,
    *,
    requests: int = 0,
    cache_hits: int = 0,
    failures: int = 0,
    error: str | None = None,
) -> None:
    """累加今天的用量。并发请求各自累加，不会互相覆盖。"""
    if not (requests or cache_hits or failures):
        return
    row = await settings(db, provider)
    values = {
        "provider": provider,
        "day": local_day(row.reset_timezone),
        "requests": requests,
        "cache_hits": cache_hits,
        "failures": failures,
        "last_error": error[:500] if error else None,
        "last_error_at": datetime.now(UTC).replace(tzinfo=None) if error else None,
    }
    table = ExternalApiUsageModel
    if db.bind.dialect.name == "mysql":
        statement = mysql_insert(table).values(values)
        new = statement.inserted
    else:
        statement = sqlite_insert(table).values(values)
        new = statement.excluded
    changes = {
        "requests": table.requests + new.requests,
        "cache_hits": table.cache_hits + new.cache_hits,
        "failures": table.failures + new.failures,
        "last_error": func.coalesce(new.last_error, table.last_error),
        "last_error_at": func.coalesce(new.last_error_at, table.last_error_at),
    }
    if db.bind.dialect.name == "mysql":
        await db.execute(statement.on_duplicate_key_update(**changes))
    else:
        await db.execute(statement.on_conflict_do_update(index_elements=["provider", "day"], set_=changes))
    if error:
        logger.warning(f"{label(provider)} 请求失败：{error[:200]}")
    if row.daily_limit and requests:
        used = await used_today(db, row)
        before = used - requests
        if before < row.daily_limit <= used:
            logger.error(f"{label(provider)} 今天的额度已用完（{used}/{row.daily_limit}），之后的请求会被跳过")
        elif before < row.daily_limit * WARN_SHARE <= used:
            logger.warning(f"{label(provider)} 今天已用 {used}/{row.daily_limit}，接近额度上限")


async def block(db: AsyncSession, provider: str, reason: str) -> None:
    """对方拒绝请求（额度用完、key 无效）：当天不再调用。"""
    row = await settings(db, provider)
    day = local_day(row.reset_timezone)
    if row.blocked_day != day:
        logger.error(f"{label(provider)} 拒绝请求，今天停止调用：{reason[:200]}")
    row.blocked_day = day
    row.blocked_reason = reason[:500]
    await db.flush()
