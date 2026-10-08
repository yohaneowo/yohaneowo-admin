from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from ..log_common import SiteCountSchema, resolve_log_order, resolve_site_filter
from .crud import BotRuntimeLogCRUD
from .model import BotRuntimeLogModel
from .schema import BotRuntimeLogBatchSchema, BotRuntimeLogOutSchema, BotRuntimeLogQueryParam


class BotRuntimeLogService:
    """运行日志：只读查询 + bot 批量写入"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def detail(self, id: int) -> BotRuntimeLogOutSchema:
        obj = await BotRuntimeLogCRUD(self.auth, self.db).get_or_404(id=id)
        return BotRuntimeLogOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BotRuntimeLogQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[BotRuntimeLogOutSchema]:
        return await BotRuntimeLogCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=resolve_log_order(order_by),
            search=resolve_site_filter(search_to_dict(search)),
            out_schema=BotRuntimeLogOutSchema,
        )

    async def site_counts(self, search: BotRuntimeLogQueryParam | None = None) -> list[SiteCountSchema]:
        """各网站的条数，除网站外的筛选条件与列表一致"""
        return await BotRuntimeLogCRUD(self.auth, self.db).count_by_site(search=search_to_dict(search))

    async def report(self, data: BotRuntimeLogBatchSchema) -> int:
        self.db.add_all(BotRuntimeLogModel(**log.model_dump(exclude={"occurred_at"}), occurred_time=log.occurred_at) for log in data.logs)
        await self.db.flush()
        return len(data.logs)
