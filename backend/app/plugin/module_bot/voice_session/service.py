from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from ..group.model import BotGroupModel
from .crud import BotVoiceSessionCRUD
from .model import BotVoiceSessionModel
from .schema import (
    BotVoiceSessionBatchSchema,
    BotVoiceSessionOutSchema,
    BotVoiceSessionQueryParam,
    BotVoiceUserStatSchema,
)

# 前端不传排序时 PaginationQueryParam 会填 [{"id": "asc"}]；默认看最新进入的
DEFAULT_ORDER: list[dict] = [{"joined_time": "desc"}, {"id": "desc"}]


class BotVoiceSessionService:
    """语音停留记录：只读查询 + bot 批量写入"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def _fill_groups(self, items: list[BotVoiceSessionOutSchema]) -> None:
        """服务器名称和头像从 bot_group 一次查出，不在记录里冗余存储"""
        ids = {i.guild_external_id for i in items}
        if not ids:
            return
        rows = await self.db.execute(select(BotGroupModel.external_id, BotGroupModel.name, BotGroupModel.icon_url).where(BotGroupModel.platform == "discord", BotGroupModel.external_id.in_(ids)))
        groups = {external_id: (name, icon_url) for external_id, name, icon_url in rows.all()}
        for item in items:
            item.group_name, item.group_icon_url = groups.get(item.guild_external_id, (None, None))

    async def detail(self, id: int) -> BotVoiceSessionOutSchema:
        obj = await BotVoiceSessionCRUD(self.auth, self.db).get_or_404(id=id)
        item = BotVoiceSessionOutSchema.model_validate(obj)
        await self._fill_groups([item])
        return item

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BotVoiceSessionQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[BotVoiceSessionOutSchema]:
        result = await BotVoiceSessionCRUD(self.auth, self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=DEFAULT_ORDER if not order_by or order_by == [{"id": "asc"}] else order_by,
            search=search_to_dict(search),
            out_schema=BotVoiceSessionOutSchema,
        )
        await self._fill_groups(result.items)
        return result

    async def user_stats(self, search: BotVoiceSessionQueryParam | None = None, limit: int = 10) -> list[BotVoiceUserStatSchema]:
        return await BotVoiceSessionCRUD(self.auth, self.db).user_stats(search=search_to_dict(search), limit=limit)

    async def report(self, data: BotVoiceSessionBatchSchema) -> int:
        self.db.add_all(
            BotVoiceSessionModel(
                **log.model_dump(exclude={"joined_at", "left_at"}),
                joined_time=log.joined_at,
                left_time=log.left_at,
            )
            for log in data.logs
        )
        await self.db.flush()
        return len(data.logs)
