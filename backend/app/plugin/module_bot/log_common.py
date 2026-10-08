"""解析日志与运行日志共用的约定。日志永久保留，不做清理。"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

# 前端不传排序时 PaginationQueryParam 会填 [{"id": "asc"}]；日志默认看最新的，改为按发生时间倒序
DEFAULT_LOG_ORDER: list[dict] = [{"occurred_time": "desc"}, {"id": "desc"}]

# bot 一次批量上报的上限，与 bot 端 services/adminApi.js 的 LOG_BATCH_SIZE 对应
MAX_LOG_BATCH = 500

# 网站筛选的特殊值：只看没有网站的日志（运行日志里与解析无关的那些，前端「其他」按钮）
OTHER_SITE = "__other__"


def resolve_log_order(order_by: list[dict] | None) -> list[dict]:
    if not order_by or order_by == [{"id": "asc"}]:
        return DEFAULT_LOG_ORDER
    return order_by


def resolve_site_filter(search: dict[str, Any] | None) -> dict[str, Any] | None:
    """search_to_dict 之后调用：把 site=OTHER_SITE 换成 IS NULL 条件"""
    if search and search.get("site") == ("eq", OTHER_SITE):
        search["site"] = ("None", None)
    return search


class SiteCountSchema(BaseModel):
    """网站按钮上的数量；site 为空表示「其他」"""

    site: str | None = Field(default=None, description="网站")
    count: int = Field(..., description="条数")


class SiteCountMixin:
    """给日志 CRUD 加按网站分组计数，筛选条件与列表查询共用 _build_conditions"""

    async def count_by_site(self, search: dict[str, Any] | None = None) -> list[SiteCountSchema]:
        search = {k: v for k, v in (search or {}).items() if k != "site"}
        conditions = await self._build_conditions(**search)  # type: ignore[attr-defined]
        site = self.model.site  # type: ignore[attr-defined]
        rows = await self.db.execute(select(site, func.count()).where(*conditions).group_by(site))  # type: ignore[attr-defined]
        return [SiteCountSchema(site=s, count=c) for s, c in rows.all()]
