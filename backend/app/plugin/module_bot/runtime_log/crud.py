from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from ..log_common import SiteCountMixin
from .model import BotRuntimeLogModel
from .schema import BotRuntimeLogReportSchema


class BotRuntimeLogCRUD(SiteCountMixin, CRUDBase[BotRuntimeLogModel, BotRuntimeLogReportSchema, BotRuntimeLogReportSchema]):
    """运行日志数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BotRuntimeLogModel, auth=auth, db=db)
