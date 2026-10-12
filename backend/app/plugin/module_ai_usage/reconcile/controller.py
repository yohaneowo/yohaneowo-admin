from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from ..anthropic_admin import admin_api_configured
from . import service
from .schema import AiCostReconcileOutSchema, AiCostReconcileRunSchema, AiCostReconcileSummarySchema

# 「立即对账」会调用外部 API，记操作日志
AiCostReconcileRouter = APIRouter(route_class=OperationLogRoute, prefix="/reconcile", tags=["AI费用对账"])


@AiCostReconcileRouter.get("/list", summary="对账结果", response_model=ResponseSchema[list[AiCostReconcileOutSchema]])
async def get_reconcile_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:reconcile:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    status: Annotated[str | None, Query(description="只看某种结果")] = None,
) -> JSONResponse:
    service.reconcile_in_background()
    result = await service.list_rows(db, status=status)
    return SuccessResponse(data=result, msg="查询对账结果成功")


@AiCostReconcileRouter.get("/summary", summary="本月对账合计", response_model=ResponseSchema[AiCostReconcileSummarySchema])
async def get_reconcile_summary_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:reconcile:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await service.summary(db)
    return SuccessResponse(data=result, msg="查询对账合计成功")


@AiCostReconcileRouter.post("/run", summary="立即对账", response_model=ResponseSchema[AiCostReconcileRunSchema])
async def run_reconcile_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:reconcile:run"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    if not admin_api_configured():
        raise CustomException(msg="还没设定 ANTHROPIC_ADMIN_API_KEY，无法对账")
    try:
        result = await service.run_now(db)
    except RuntimeError as error:
        raise CustomException(msg=str(error)) from error
    return SuccessResponse(data=result, msg="对账完成")
