from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import BotVoiceSessionModel
from .schema import BotVoiceSessionReportSchema, BotVoiceUserStatSchema


class BotVoiceSessionCRUD(CRUDBase[BotVoiceSessionModel, BotVoiceSessionReportSchema, BotVoiceSessionReportSchema]):
    """语音停留记录数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BotVoiceSessionModel, auth=auth, db=db)

    async def user_stats(self, search: dict[str, Any] | None = None, limit: int = 10) -> list[BotVoiceUserStatSchema]:
        """按用户加总停留时长，筛选条件与列表查询相同"""
        conditions = await self._build_conditions(**(search or {}))
        model = self.model
        totals = (
            select(
                model.user_external_id,
                func.sum(model.duration_seconds).label("total_seconds"),
                func.count().label("session_count"),
                func.max(model.id).label("latest_id"),
            )
            .where(*conditions)
            .group_by(model.user_external_id)
            .order_by(func.sum(model.duration_seconds).desc())
            .limit(limit)
            .subquery()
        )
        # 名称和头像取每个用户最近一条记录的，改过的话显示现在的
        rows = await self.db.execute(
            select(totals.c.user_external_id, model.user_name, model.user_avatar_url, totals.c.total_seconds, totals.c.session_count)
            .join(model, model.id == totals.c.latest_id)
            .order_by(totals.c.total_seconds.desc())
        )
        return [
            BotVoiceUserStatSchema(user_external_id=user_id, user_name=name, user_avatar_url=avatar, total_seconds=int(total or 0), session_count=count)
            for user_id, name, avatar, total, count in rows.all()
        ]
