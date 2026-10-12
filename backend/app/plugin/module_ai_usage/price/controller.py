from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from ..pricing import refresh_prices_in_background
from .schema import AiModelPriceOutSchema, AiModelPriceQueryParam, AiModelPriceSaveSchema, AiPriceStatusSchema, AiPriceSyncResultSchema
from .service import AiModelPriceService

# 修改类操作记操作日志
AiModelPriceRouter = APIRouter(route_class=OperationLogRoute, prefix="/model-price", tags=["AI模型单价"])


@AiModelPriceRouter.get("/list", summary="查询模型单价", response_model=ResponseSchema[PageResultSchema[AiModelPriceOutSchema]])
async def get_model_price_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:model_price:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[AiModelPriceQueryParam, Query()],
) -> JSONResponse:
    refresh_prices_in_background()
    result = await AiModelPriceService(auth, db).page(page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by)
    return SuccessResponse(data=result, msg="查询模型单价成功")


@AiModelPriceRouter.get("/status", summary="价格表状态", response_model=ResponseSchema[AiPriceStatusSchema])
async def get_model_price_status_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:model_price:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await AiModelPriceService(auth, db).status()
    return SuccessResponse(data=result, msg="查询价格表状态成功")


@AiModelPriceRouter.post("/create", summary="手动新增模型单价", response_model=ResponseSchema[AiModelPriceOutSchema])
async def create_model_price_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:model_price:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[AiModelPriceSaveSchema, Body()],
) -> JSONResponse:
    result = await AiModelPriceService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="新增模型单价成功")


@AiModelPriceRouter.put("/update/{id}", summary="修改模型单价", response_model=ResponseSchema[AiModelPriceOutSchema])
async def update_model_price_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:model_price:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="单价ID", ge=1)],
    data: Annotated[AiModelPriceSaveSchema, Body()],
) -> JSONResponse:
    result = await AiModelPriceService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改模型单价成功")


@AiModelPriceRouter.post("/sync", summary="立即从 LiteLLM 同步单价", response_model=ResponseSchema[AiPriceSyncResultSchema])
async def sync_model_price_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ai_usage:model_price:sync"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await AiModelPriceService(auth, db).sync()
    return SuccessResponse(data=result, msg="同步模型单价成功")
