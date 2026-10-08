from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from ..group.model import BotGroupModel
from ..log_common import SiteCountSchema, resolve_log_order, resolve_site_filter
from .crud import BotParseLogCRUD
from .model import BotParseLogModel
from .schema import BotParseLogBatchSchema, BotParseLogOutSchema, BotParseLogQueryParam


class BotParseLogService:
    """解析日志：只读查询 + bot 批量写入"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def _fill_group_names(self, items: list[BotParseLogOutSchema]) -> None:
        """按 (platform, external_id) 一次查出群组名称，不在日志里冗余存储"""
        keys = {(i.platform, i.group_external_id) for i in items if i.group_external_id}
        if not keys:
            return
        rows = await self.db.execute(select(BotGroupModel.platform, BotGroupModel.external_id, BotGroupModel.name).where(tuple_(BotGroupModel.platform, BotGroupModel.external_id).in_(keys)))
        names = {(platform, external_id): name for platform, external_id, name in rows.all()}
        for item in items:
            item.group_name = names.get((item.platform, item.group_external_id))

    async def detail(self, id: int) -> BotParseLogOutSchema:
        obj = await BotParseLogCRUD(self.auth, self.db).get_or_404(id=id)
        item = BotParseLogOutSchema.model_validate(obj)
        await self._fill_group_names([item])
        return item

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BotParseLogQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[BotParseLogOutSchema]:
        result = await BotParseLogCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=resolve_log_order(order_by),
            search=resolve_site_filter(search_to_dict(search)),
            out_schema=BotParseLogOutSchema,
        )
        await self._fill_group_names(result.items)
        return result

    async def site_counts(self, search: BotParseLogQueryParam | None = None) -> list[SiteCountSchema]:
        """各网站的条数，除网站外的筛选条件与列表一致"""
        return await BotParseLogCRUD(self.auth, self.db).count_by_site(search=search_to_dict(search))

    async def report(self, data: BotParseLogBatchSchema) -> int:
        self.db.add_all(BotParseLogModel(**log.model_dump(exclude={"occurred_at"}), occurred_time=log.occurred_at) for log in data.logs)
        await self.db.flush()
        return len(data.logs)
