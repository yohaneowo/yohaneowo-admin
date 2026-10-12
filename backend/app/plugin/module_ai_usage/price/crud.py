from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import AiModelPriceModel
from .schema import AiModelPriceSaveSchema


class AiModelPriceCRUD(CRUDBase[AiModelPriceModel, AiModelPriceSaveSchema, AiModelPriceSaveSchema]):
    """模型单价数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=AiModelPriceModel, auth=auth, db=db)
