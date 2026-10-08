from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import BotGroupModel
from .schema import BotGroupUpdateSchema


class BotGroupCRUD(CRUDBase[BotGroupModel, BotGroupUpdateSchema, BotGroupUpdateSchema]):
    """Bot 群组数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BotGroupModel, auth=auth, db=db)
