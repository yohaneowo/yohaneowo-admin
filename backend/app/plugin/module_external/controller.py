import os
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from . import service
from .model import ExternalApiProviderModel, ExternalApiUsageModel
from .schema import ExternalProviderOutSchema, ExternalProviderSaveSchema, ExternalUsageDaySchema, ExternalUsageTodaySchema

# 修改类操作记操作日志
ExternalUsageRouter = APIRouter(route_class=OperationLogRoute, prefix="/usage", tags=["第三方接口"])
Db = Annotated[AsyncSession, Depends(db_getter)]


async def provider_out(db: AsyncSession, row: ExternalApiProviderModel) -> ExternalProviderOutSchema:
    day = service.local_day(row.reset_timezone)
    usage = await db.get(ExternalApiUsageModel, (row.provider, day))
    env = service.PROVIDERS.get(row.provider, {}).get("env")
    return ExternalProviderOutSchema(
        provider=row.provider,
        label=service.label(row.provider),
        daily_limit=row.daily_limit,
        reset_timezone=row.reset_timezone,
        note=row.note,
        configured=not env or bool(os.environ.get(env)),
        blocked=row.blocked_day == day,
        blocked_reason=row.blocked_reason if row.blocked_day == day else None,
        today=ExternalUsageTodaySchema.model_validate(usage, from_attributes=True) if usage else ExternalUsageTodaySchema(),
    )


@ExternalUsageRouter.get("/list", summary="各服务今天的用量与额度", response_model=ResponseSchema[list[ExternalProviderOutSchema]])
async def list_usage(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_external:usage:query"]))],
    db: Db,
) -> JSONResponse:
    for provider in service.PROVIDERS:
        await service.settings(db, provider)
    rows = await db.scalars(select(ExternalApiProviderModel).order_by(ExternalApiProviderModel.provider))
    return SuccessResponse(data=[(await provider_out(db, row)).model_dump(mode="json") for row in rows], msg="查询第三方接口用量成功")


@ExternalUsageRouter.get("/history/{provider}", summary="某个服务最近几天的用量", response_model=ResponseSchema[list[ExternalUsageDaySchema]])
async def usage_history(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_external:usage:query"]))],
    db: Db,
    provider: Annotated[str, Path(max_length=32)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> JSONResponse:
    row = await service.settings(db, provider)
    since = service.local_day(row.reset_timezone) - timedelta(days=days - 1)
    rows = await db.scalars(select(ExternalApiUsageModel).where(ExternalApiUsageModel.provider == provider, ExternalApiUsageModel.day >= since).order_by(ExternalApiUsageModel.day.desc()))
    data = [ExternalUsageDaySchema.model_validate(item, from_attributes=True).model_dump(mode="json") for item in rows]
    return SuccessResponse(data=data, msg="查询用量历史成功")


@ExternalUsageRouter.put("/provider/{provider}", summary="修改额度设定", response_model=ResponseSchema[ExternalProviderOutSchema])
async def save_provider(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_external:usage:update"]))],
    db: Db,
    provider: Annotated[str, Path(max_length=32)],
    data: Annotated[ExternalProviderSaveSchema, Body()],
) -> JSONResponse:
    row = await db.get(ExternalApiProviderModel, provider)
    if row is None:
        raise HTTPException(404, "没有这个服务")
    row.daily_limit, row.reset_timezone, row.note = data.daily_limit, data.reset_timezone, data.note
    row.blocked_day, row.blocked_reason = None, None
    await db.flush()
    return SuccessResponse(data=(await provider_out(db, row)).model_dump(mode="json"), msg="已保存额度设定")
