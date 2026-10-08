from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter

from ..internal_auth import verify_bot_token
from ..log_common import SiteCountSchema
from .schema import BotParseLogBatchSchema, BotParseLogOutSchema, BotParseLogQueryParam
from .service import BotParseLogService

# 只读查询，不挂 OperationLogRoute：后台页面会自动刷新，每次查询都记操作日志只会刷屏
BotParseLogRouter = APIRouter(prefix="/parse-log", tags=["Bot解析日志"])


@BotParseLogRouter.get("/detail/{id}", summary="获取解析日志详情", response_model=ResponseSchema[BotParseLogOutSchema])
async def get_parse_log_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:parse_log:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="日志ID", ge=1)],
) -> JSONResponse:
    result = await BotParseLogService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取解析日志详情成功")


@BotParseLogRouter.get("/list", summary="查询解析日志", response_model=ResponseSchema[PageResultSchema[BotParseLogOutSchema]])
async def get_parse_log_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:parse_log:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BotParseLogQueryParam, Query()],
) -> JSONResponse:
    result = await BotParseLogService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询解析日志成功")


@BotParseLogRouter.get("/site-stats", summary="解析日志各网站条数", response_model=ResponseSchema[list[SiteCountSchema]])
async def get_parse_log_site_stats_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:parse_log:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[BotParseLogQueryParam, Query()],
) -> JSONResponse:
    result = await BotParseLogService(auth, db).site_counts(search=search)
    return SuccessResponse(data=result, msg="查询各网站条数成功")


BotParseLogInternalRouter = APIRouter(
    prefix="/internal/parse-log",
    tags=["Bot内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@BotParseLogInternalRouter.post("/report", summary="批量上报解析日志", response_model=ResponseSchema[int])
async def report_parse_log_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotParseLogBatchSchema, Body()],
) -> JSONResponse:
    count = await BotParseLogService(AuthSchema(), db).report(data=data)
    return SuccessResponse(data=count, msg="上报解析日志成功")
