from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.plugin.module_bot.internal_auth import verify_bot_token

from ..pricing import refresh_prices_in_background
from ..reconcile.service import reconcile_in_background
from .schema import AiCallBatchSchema, AiCallFiltersSchema, AiCallLogOutSchema, AiCallLogQueryParam, AiCallSummarySchema, AiCostRankingQueryParam, AiCostRankingRowSchema
from .service import AiCallLogService

# 只读查询，不挂 OperationLogRoute（页面会自动刷新，每次查询都记操作日志只会刷屏）
AiCallLogRouter = APIRouter(prefix="/call-log", tags=["AI调用日志"])


@AiCallLogRouter.get("/detail/{id}", summary="获取调用记录详情", response_model=ResponseSchema[AiCallLogOutSchema])
async def get_call_log_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:call_log:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="记录ID", ge=1)],
) -> JSONResponse:
    result = await AiCallLogService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取调用记录详情成功")


@AiCallLogRouter.get("/filters", summary="搜索栏选项（项目、功能、供应商、模型、平台）", response_model=ResponseSchema[AiCallFiltersSchema])
async def get_call_log_filters_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:call_log:query", "module_ai_usage:ranking:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await AiCallLogService(auth, db).filters()
    return SuccessResponse(data=result, msg="查询搜索栏选项成功")


@AiCallLogRouter.get("/list", summary="查询调用记录", response_model=ResponseSchema[PageResultSchema[AiCallLogOutSchema]])
async def get_call_log_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:call_log:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[AiCallLogQueryParam, Query()],
) -> JSONResponse:
    result = await AiCallLogService(auth, db).page(page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by)
    return SuccessResponse(data=result, msg="查询调用记录成功")


@AiCallLogRouter.get("/summary", summary="调用记录合计", response_model=ResponseSchema[AiCallSummarySchema])
async def get_call_log_summary_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:call_log:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[AiCallLogQueryParam, Query()],
) -> JSONResponse:
    result = await AiCallLogService(auth, db).summary(search=search)
    return SuccessResponse(data=result, msg="查询调用记录合计成功")


@AiCallLogRouter.get("/ranking", summary="AI 费用排行", response_model=ResponseSchema[list[AiCostRankingRowSchema]])
async def get_call_log_ranking_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:ranking:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[AiCostRankingQueryParam, Query()],
) -> JSONResponse:
    result = await AiCallLogService(auth, db).ranking(group_by=search.group_by, search=search.filters(), limit=search.limit)
    return SuccessResponse(data=result, msg="查询费用排行成功")


# ── 各项目上报：第一版与 bot 共用 X-Bot-Token（BOT_API_TOKEN） ──
AiCallLogInternalRouter = APIRouter(
    prefix="/internal/call",
    tags=["AI调用日志内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@AiCallLogInternalRouter.post("/report", summary="批量上报 AI 调用", response_model=ResponseSchema[int])
async def report_call_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[AiCallBatchSchema, Body()],
) -> JSONResponse:
    count = await AiCallLogService(AuthSchema(), db).report(data=data)
    # 价格表过期、或该对账了，就顺便在背景处理（各项目每天都会上报，等于每天各做一次）
    refresh_prices_in_background()
    reconcile_in_background()
    return SuccessResponse(data=count, msg="上报 AI 调用成功")
