from decimal import Decimal

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from ..pricing import call_cost, load_price_map
from .crud import AiCallLogCRUD
from .model import AiCallLogModel
from .schema import (
    AiCallBatchSchema,
    AiCallFiltersSchema,
    AiCallLogOutSchema,
    AiCallLogQueryParam,
    AiCallSummarySchema,
    AiCostRankingRowSchema,
    RankingGroup,
)

# 前端不传排序时 PaginationQueryParam 会填 [{"id": "asc"}]；默认看最新的
DEFAULT_ORDER: list[dict] = [{"occurred_time": "desc"}, {"id": "desc"}]
GROUP_COLUMNS = {
    "project": AiCallLogModel.project,
    "feature": AiCallLogModel.feature,
    "model": AiCallLogModel.model,
}


class AiCallLogService:
    """AI 调用记录：查询、合计、费用排行，以及各项目批量上报"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def _conditions(self, search: AiCallLogQueryParam | None) -> list:
        return await AiCallLogCRUD(self.auth, self.db)._build_conditions(**(search_to_dict(search) or {}))

    async def detail(self, id: int) -> AiCallLogOutSchema:
        obj = await AiCallLogCRUD(self.auth, self.db).get_or_404(id=id)
        return AiCallLogOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: AiCallLogQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[AiCallLogOutSchema]:
        return await AiCallLogCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=DEFAULT_ORDER if not order_by or order_by == [{"id": "asc"}] else order_by,
            search=search_to_dict(search),
            out_schema=AiCallLogOutSchema,
        )

    async def filters(self) -> AiCallFiltersSchema:
        async def values(column) -> list[str]:
            rows = await self.db.execute(select(distinct(column)).where(AiCallLogModel.is_deleted.is_(False), column.is_not(None)).order_by(column))
            return list(rows.scalars().all())

        model = AiCallLogModel
        return AiCallFiltersSchema(
            projects=await values(model.project),
            features=await values(model.feature),
            providers=await values(model.provider),
            models=await values(model.model),
            platforms=await values(model.user_platform),
        )

    async def summary(self, search: AiCallLogQueryParam | None = None) -> AiCallSummarySchema:
        model = AiCallLogModel
        row = (
            await self.db.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(model.cost_usd), 0),
                    func.coalesce(func.sum(model.input_tokens), 0),
                    func.coalesce(func.sum(model.output_tokens), 0),
                    func.coalesce(func.sum(case((model.cost_usd.is_(None), 1), else_=0)), 0),
                ).where(*await self._conditions(search))
            )
        ).one()
        return AiCallSummarySchema(
            call_count=row[0],
            total_cost_usd=Decimal(row[1]),
            input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            unpriced_count=int(row[4]),
        )

    async def ranking(self, group_by: RankingGroup, search: AiCallLogQueryParam | None = None, limit: int = 20) -> list[AiCostRankingRowSchema]:
        """按用户 / 项目 / 功能 / 模型加总费用，费用高的在前"""
        model = AiCallLogModel
        conditions = await self._conditions(search)
        keys = [model.user_platform, model.user_external_id] if group_by == "user" else [GROUP_COLUMNS[group_by]]
        cost = func.coalesce(func.sum(model.cost_usd), 0)
        totals = (
            select(
                *keys,
                func.count().label("call_count"),
                cost.label("total_cost"),
                func.coalesce(func.sum(model.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(model.output_tokens), 0).label("output_tokens"),
                func.max(model.occurred_time).label("last_time"),
                func.max(model.id).label("latest_id"),
            )
            .where(*conditions)
            .group_by(*keys)
            .order_by(cost.desc())
            .limit(limit)
            .subquery()
        )
        if group_by != "user":
            rows = await self.db.execute(select(totals).order_by(totals.c.total_cost.desc()))
            return [
                AiCostRankingRowSchema(
                    key=row[0],
                    call_count=row.call_count,
                    total_cost_usd=Decimal(row.total_cost),
                    input_tokens=int(row.input_tokens),
                    output_tokens=int(row.output_tokens),
                    last_time=row.last_time,
                )
                for row in rows.all()
            ]

        # 名称和头像取每个用户最近一条记录的，改过的话显示现在的
        rows = await self.db.execute(select(totals, model.user_name, model.user_avatar_url).join(model, model.id == totals.c.latest_id).order_by(totals.c.total_cost.desc()))
        return [
            AiCostRankingRowSchema(
                key=f"{row.user_platform}:{row.user_external_id or ''}",
                user_platform=row.user_platform,
                user_external_id=row.user_external_id,
                user_name=row.user_name if row.user_platform != "system" else (row.user_name or "系统（共用）"),
                user_avatar_url=row.user_avatar_url,
                call_count=row.call_count,
                total_cost_usd=Decimal(row.total_cost),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                last_time=row.last_time,
            )
            for row in rows.all()
        ]

    async def report(self, data: AiCallBatchSchema) -> int:
        """写入一批调用，按当下单价算好费用；没有单价的先留空，等价格表补上再补算"""
        price_map = await load_price_map(self.db)
        for log in data.logs:
            call = AiCallLogModel(**log.model_dump(exclude={"occurred_at"}), occurred_time=log.occurred_at)
            price = price_map.get((call.provider, call.model))
            call.cost_usd = call_cost(price, call) if price is not None else None
            self.db.add(call)
        await self.db.flush()
        return len(data.logs)
