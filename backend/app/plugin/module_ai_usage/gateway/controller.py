from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import SuccessResponse
from app.core.dependencies import db_getter
from app.plugin.module_bot.internal_auth import verify_bot_token

from ..pricing import refresh_prices_in_background
from . import service
from .schema import AiGatewayRequestSchema

# ── 各项目经 admin 调用 AI：与上报共用 X-Bot-Token（BOT_API_TOKEN） ──
# 回应 {status, body}：status 是供应商自己的 HTTP 状态（连不上为 null），body 是供应商的回应原文
AiGatewayInternalRouter = APIRouter(prefix="/internal/ai", tags=["AI转发内部接口"], dependencies=[Depends(verify_bot_token)])
Db = Annotated[AsyncSession, Depends(db_getter)]


@AiGatewayInternalRouter.post("/messages", summary="转发 Claude Messages API")
async def claude_messages(db: Db, data: Annotated[AiGatewayRequestSchema, Body()]) -> JSONResponse:
    try:
        status, body = await service.claude(db, data)
    except service.AiNotConfigured as error:
        raise HTTPException(503, str(error)) from error
    refresh_prices_in_background()
    return SuccessResponse(data={"status": status, "body": body})


@AiGatewayInternalRouter.post("/embeddings", summary="转发 Voyage embeddings")
async def voyage_embeddings(db: Db, data: Annotated[AiGatewayRequestSchema, Body()]) -> JSONResponse:
    try:
        status, body = await service.voyage(db, data)
    except service.AiNotConfigured as error:
        raise HTTPException(503, str(error)) from error
    return SuccessResponse(data={"status": status, "body": body})
