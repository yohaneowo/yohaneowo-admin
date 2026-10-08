from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import BotPortfolioSnapshotModel
from .schema import BotPortfolioReportSchema


class BotPortfolioSnapshotCRUD(CRUDBase[BotPortfolioSnapshotModel, BotPortfolioReportSchema, BotPortfolioReportSchema]):
    """资产快照数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BotPortfolioSnapshotModel, auth=auth, db=db)
