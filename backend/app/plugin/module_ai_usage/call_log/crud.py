from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import AiCallLogModel
from .schema import AiCallReportSchema


class AiCallLogCRUD(CRUDBase[AiCallLogModel, AiCallReportSchema, AiCallReportSchema]):
    """AI 调用记录数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=AiCallLogModel, auth=auth, db=db)
