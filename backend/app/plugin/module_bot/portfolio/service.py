from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from ..log_common import resolve_log_order
from .crud import BotPortfolioSnapshotCRUD
from .model import BotPortfolioSnapshotModel
from .schema import BotPortfolioLatestSchema, BotPortfolioQueryParam, BotPortfolioReportSchema, BotPortfolioSnapshotOutSchema

COMPARE_WINDOW = timedelta(hours=24)


class BotPortfolioService:
    """资产快照：首页卡片、历史列表 + bot 上报"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def _latest_before(self, before=None) -> BotPortfolioSnapshotModel | None:
        model = BotPortfolioSnapshotModel
        sql = select(model).where(model.is_deleted.is_(False))
        if before is not None:
            sql = sql.where(model.occurred_time <= before)
        result = await self.db.execute(sql.order_by(model.occurred_time.desc(), model.id.desc()).limit(1))
        return result.scalars().first()

    async def latest(self) -> BotPortfolioLatestSchema:
        latest = await self._latest_before()
        if latest is None:
            return BotPortfolioLatestSchema()
        previous = await self._latest_before(latest.occurred_time - COMPARE_WINDOW)
        out = BotPortfolioLatestSchema(latest=BotPortfolioSnapshotOutSchema.model_validate(latest))
        if previous is not None:
            out.previous_total_twd = float(previous.total_twd)
            if previous.total_twd:
                out.change_percent = round((float(latest.total_twd) / float(previous.total_twd) - 1) * 100, 2)
        return out

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BotPortfolioQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[BotPortfolioSnapshotOutSchema]:
        return await BotPortfolioSnapshotCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=resolve_log_order(order_by),
            search=search_to_dict(search),
            out_schema=BotPortfolioSnapshotOutSchema,
        )

    async def report(self, data: BotPortfolioReportSchema) -> None:
        payload = data.model_dump(exclude={"occurred_at"})
        self.db.add(BotPortfolioSnapshotModel(**payload, occurred_time=data.occurred_at))
        await self.db.flush()
