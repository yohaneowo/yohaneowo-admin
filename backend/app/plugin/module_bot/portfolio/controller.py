from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter

from ..internal_auth import verify_bot_token
from .schema import BotPortfolioLatestSchema, BotPortfolioQueryParam, BotPortfolioReportSchema, BotPortfolioSnapshotOutSchema
from .service import BotPortfolioService

# 总资产属敏感信息：首页卡片与「资产记录」页共用 module_bot:portfolio:query，只授予需要的角色
BotPortfolioRouter = APIRouter(prefix="/portfolio", tags=["Bot资产"])


@BotPortfolioRouter.get("/latest", summary="最新总资产", response_model=ResponseSchema[BotPortfolioLatestSchema])
async def get_portfolio_latest_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:portfolio:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await BotPortfolioService(auth, db).latest()
    return SuccessResponse(data=result, msg="查询最新总资产成功")


@BotPortfolioRouter.get("/list", summary="查询资产快照", response_model=ResponseSchema[PageResultSchema[BotPortfolioSnapshotOutSchema]])
async def get_portfolio_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:portfolio:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BotPortfolioQueryParam, Query()],
) -> JSONResponse:
    result = await BotPortfolioService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询资产快照成功")


BotPortfolioInternalRouter = APIRouter(
    prefix="/internal/portfolio",
    tags=["Bot内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@BotPortfolioInternalRouter.post("/report", summary="上报资产快照", response_model=ResponseSchema[None])
async def report_portfolio_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotPortfolioReportSchema, Body()],
) -> JSONResponse:
    await BotPortfolioService(AuthSchema(), db).report(data=data)
    return SuccessResponse(msg="上报资产快照成功")
