from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from ..log_common import SiteCountMixin
from .model import BotParseLogModel
from .schema import BotParseLogReportSchema


class BotParseLogCRUD(SiteCountMixin, CRUDBase[BotParseLogModel, BotParseLogReportSchema, BotParseLogReportSchema]):
    """解析日志数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BotParseLogModel, auth=auth, db=db)
