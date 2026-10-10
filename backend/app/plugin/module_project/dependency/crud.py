from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import ProjectDependencyModel
from .schema import DependencyReportItemSchema


class ProjectDependencyCRUD(CRUDBase[ProjectDependencyModel, DependencyReportItemSchema, DependencyReportItemSchema]):
    """项目依赖数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ProjectDependencyModel, auth=auth, db=db)
