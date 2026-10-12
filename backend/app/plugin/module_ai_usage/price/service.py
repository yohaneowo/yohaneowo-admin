from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from ..call_log.model import AiCallLogModel
from ..pricing import last_synced_time, reprice_unpriced, sync_prices
from .crud import AiModelPriceCRUD
from .model import AiModelPriceModel
from .schema import (
    AiModelPriceOutSchema,
    AiModelPriceQueryParam,
    AiModelPriceSaveSchema,
    AiPriceStatusSchema,
    AiPriceSyncResultSchema,
)

DEFAULT_ORDER: list[dict] = [{"provider": "asc"}, {"model": "asc"}]


class AiModelPriceService:
    """模型单价：查询、手动维护、从 LiteLLM 同步"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: AiModelPriceQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[AiModelPriceOutSchema]:
        return await AiModelPriceCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=DEFAULT_ORDER if not order_by or order_by == [{"id": "asc"}] else order_by,
            search=search_to_dict(search),
            out_schema=AiModelPriceOutSchema,
        )

    async def status(self) -> AiPriceStatusSchema:
        model_count = (await self.db.execute(select(func.count()).select_from(AiModelPriceModel).where(AiModelPriceModel.is_deleted.is_(False)))).scalar() or 0
        unpriced = (await self.db.execute(select(func.count()).select_from(AiCallLogModel).where(AiCallLogModel.cost_usd.is_(None), AiCallLogModel.is_deleted.is_(False)))).scalar() or 0
        providers = await self.db.execute(select(distinct(AiModelPriceModel.provider)).where(AiModelPriceModel.is_deleted.is_(False)).order_by(AiModelPriceModel.provider))
        return AiPriceStatusSchema(
            last_synced_time=await last_synced_time(self.db),
            model_count=model_count,
            unpriced_call_count=unpriced,
            providers=list(providers.scalars().all()),
        )

    async def _find(self, provider: str, model: str) -> AiModelPriceModel | None:
        rows = await self.db.execute(select(AiModelPriceModel).where(AiModelPriceModel.provider == provider, AiModelPriceModel.model == model, AiModelPriceModel.is_deleted.is_(False)))
        return rows.scalars().first()

    async def create(self, data: AiModelPriceSaveSchema) -> AiModelPriceOutSchema:
        """手动新增价格表里没有的模型，并补算它之前未定价的调用"""
        if await self._find(data.provider, data.model):
            raise CustomException(msg=f"{data.provider} / {data.model} 已经有单价了，请直接修改")
        obj = AiModelPriceModel(**data.model_dump(), source="manual")
        self.db.add(obj)
        await self.db.flush()
        await reprice_unpriced(self.db)
        return AiModelPriceOutSchema.model_validate(obj)

    async def update(self, id: int, data: AiModelPriceSaveSchema) -> AiModelPriceOutSchema:
        """手动修改单价。改过的默认锁定，不再被自动同步覆盖；已算好的历史费用不变"""
        obj = await AiModelPriceCRUD(self.auth, self.db).get_or_404(id=id, msg="修改失败，该模型单价不存在")
        for field, value in data.model_dump(exclude={"provider", "model"}).items():
            setattr(obj, field, value)
        obj.source = "manual"
        await self.db.flush()
        await reprice_unpriced(self.db)
        return AiModelPriceOutSchema.model_validate(obj)

    async def sync(self) -> AiPriceSyncResultSchema:
        try:
            result = await sync_prices(self.db)
        except Exception as error:
            raise CustomException(msg=f"从 LiteLLM 同步单价失败：{error}") from error
        return AiPriceSyncResultSchema(**result)
