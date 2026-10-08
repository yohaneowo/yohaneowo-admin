from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, BatchSetAvailable, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import BotGroupCRUD
from .model import BotGroupModel
from .schema import (
    BotGroupJoinSchema,
    BotGroupOutSchema,
    BotGroupQueryParam,
    BotGroupRefSchema,
    BotGroupReportSchema,
    BotGroupStateSchema,
    BotGroupSyncSchema,
    BotGroupUpdateSchema,
)


class BotGroupService:
    """Bot 群组：后台查看与启停，以及 bot 上报的同步逻辑"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    # ── 后台 ──────────────────────────────────────────────────────

    async def detail(self, id: int) -> BotGroupOutSchema:
        obj = await BotGroupCRUD(self.auth, self.db).get_or_404(id=id)
        return BotGroupOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BotGroupQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[BotGroupOutSchema]:
        offset = (page_no - 1) * page_size
        return await BotGroupCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=BotGroupOutSchema,
        )

    async def update(self, id: int, data: BotGroupUpdateSchema) -> BotGroupOutSchema:
        await BotGroupCRUD(self.auth, self.db).get_or_404(id=id, msg="更新失败，该群组不存在")
        await BotGroupCRUD(self.auth, self.db).update(id=id, data=data)
        return await self.detail(id=id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await BotGroupCRUD(self.auth, self.db).delete(ids=ids)

    async def set_available(self, data: BatchSetAvailable) -> None:
        await BotGroupCRUD(self.auth, self.db).set(ids=data.ids, status=data.status)

    # ── bot 内部 API ──────────────────────────────────────────────

    async def _upsert(self, platform: str, report: BotGroupReportSchema, now: datetime) -> BotGroupModel:
        crud = BotGroupCRUD(self.auth, self.db)
        obj = await crud.get(platform=platform, external_id=report.external_id)
        fields = report.model_dump(exclude={"external_id"}, exclude_none=True)
        if obj is None:
            return await crud.create({"platform": platform, "external_id": report.external_id, **fields, "is_joined": True, "joined_time": now})
        if not obj.is_joined:
            fields.update(is_joined=True, joined_time=now, left_time=None)
        for key, value in fields.items():
            setattr(obj, key, value)
        await self.db.flush()
        return obj

    async def sync(self, data: BotGroupSyncSchema) -> list[BotGroupStateSchema]:
        now = datetime.now(UTC)
        reported = {g.external_id: g for g in data.groups}
        existing = await BotGroupCRUD(self.auth, self.db).get_list(search={"platform": ("eq", data.platform)})
        for obj in existing:
            if obj.external_id not in reported and obj.is_joined:
                obj.is_joined = False
                obj.left_time = now
        objs = [await self._upsert(data.platform, report, now) for report in reported.values()]
        await self.db.flush()
        return [BotGroupStateSchema.model_validate(obj) for obj in objs]

    async def join(self, data: BotGroupJoinSchema) -> BotGroupStateSchema:
        obj = await self._upsert(data.platform, data, datetime.now(UTC))
        return BotGroupStateSchema.model_validate(obj)

    async def leave(self, data: BotGroupRefSchema) -> None:
        obj = await BotGroupCRUD(self.auth, self.db).get(platform=data.platform, external_id=data.external_id)
        if obj and obj.is_joined:
            obj.is_joined = False
            obj.left_time = datetime.now(UTC)
            await self.db.flush()

    async def touch(self, data: BotGroupRefSchema) -> bool:
        """更新最后活动时间；群组还没登记过时返回 False，由 bot 改调 join 补登记"""
        obj = await BotGroupCRUD(self.auth, self.db).get(platform=data.platform, external_id=data.external_id)
        if obj is None:
            return False
        obj.last_active_time = datetime.now(UTC)
        await self.db.flush()
        return True
