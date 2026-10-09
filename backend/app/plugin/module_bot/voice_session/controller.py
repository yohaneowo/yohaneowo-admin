from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter

from ..internal_auth import verify_bot_token
from .schema import BotVoiceSessionBatchSchema, BotVoiceSessionOutSchema, BotVoiceSessionQueryParam, BotVoiceUserStatSchema
from .service import BotVoiceSessionService

# 只读查询，不挂 OperationLogRoute（同解析日志：页面会自动刷新）
BotVoiceSessionRouter = APIRouter(prefix="/voice-session", tags=["Bot语音记录"])


@BotVoiceSessionRouter.get("/detail/{id}", summary="获取语音停留记录详情", response_model=ResponseSchema[BotVoiceSessionOutSchema])
async def get_voice_session_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:voice_session:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="记录ID", ge=1)],
) -> JSONResponse:
    result = await BotVoiceSessionService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取语音停留记录详情成功")


@BotVoiceSessionRouter.get("/list", summary="查询语音停留记录", response_model=ResponseSchema[PageResultSchema[BotVoiceSessionOutSchema]])
async def get_voice_session_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:voice_session:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BotVoiceSessionQueryParam, Query()],
) -> JSONResponse:
    result = await BotVoiceSessionService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询语音停留记录成功")


@BotVoiceSessionRouter.get("/user-stats", summary="语音时长排行", response_model=ResponseSchema[list[BotVoiceUserStatSchema]])
async def get_voice_session_user_stats_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:voice_session:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[BotVoiceSessionQueryParam, Query()],
    limit: Annotated[int, Query(description="前几名", ge=1, le=50)] = 10,
) -> JSONResponse:
    result = await BotVoiceSessionService(auth, db).user_stats(search=search, limit=limit)
    return SuccessResponse(data=result, msg="查询语音时长排行成功")


BotVoiceSessionInternalRouter = APIRouter(
    prefix="/internal/voice-session",
    tags=["Bot内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@BotVoiceSessionInternalRouter.post("/report", summary="批量上报语音停留记录", response_model=ResponseSchema[int])
async def report_voice_session_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotVoiceSessionBatchSchema, Body()],
) -> JSONResponse:
    count = await BotVoiceSessionService(AuthSchema(), db).report(data=data)
    return SuccessResponse(data=count, msg="上报语音停留记录成功")
