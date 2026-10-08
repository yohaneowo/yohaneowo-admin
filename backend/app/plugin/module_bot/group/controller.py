from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, BatchSetAvailable, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from ..internal_auth import verify_bot_token
from .schema import (
    BotGroupJoinSchema,
    BotGroupOutSchema,
    BotGroupQueryParam,
    BotGroupRefSchema,
    BotGroupStateSchema,
    BotGroupSyncSchema,
    BotGroupUpdateSchema,
)
from .service import BotGroupService

BotGroupRouter = APIRouter(route_class=OperationLogRoute, prefix="/group", tags=["Bot群组"])


@BotGroupRouter.get("/detail/{id}", summary="获取群组详情", response_model=ResponseSchema[BotGroupOutSchema])
async def get_group_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:group:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="群组ID", ge=1)],
) -> JSONResponse:
    result = await BotGroupService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取群组详情成功")


@BotGroupRouter.get("/list", summary="查询群组", response_model=ResponseSchema[PageResultSchema[BotGroupOutSchema]])
async def get_group_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:group:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BotGroupQueryParam, Query()],
) -> JSONResponse:
    result = await BotGroupService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询群组列表成功")


@BotGroupRouter.put("/update/{id}", summary="修改群组", response_model=ResponseSchema[BotGroupOutSchema])
async def update_group_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:group:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="群组ID", ge=1)],
    data: Annotated[BotGroupUpdateSchema, Body(description="群组修改参数")],
) -> JSONResponse:
    result = await BotGroupService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改群组成功")


@BotGroupRouter.delete("/delete", summary="删除群组", response_model=ResponseSchema[None])
async def delete_group_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:group:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await BotGroupService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除群组成功")


@BotGroupRouter.patch("/status/batch", summary="批量修改群组状态", response_model=ResponseSchema[None])
async def batch_set_available_group_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_bot:group:patch"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BatchSetAvailable, Body(description="状态设置")],
) -> JSONResponse:
    await BotGroupService(auth, db).set_available(data=data)
    return SuccessResponse(msg="批量修改群组状态成功")


# ── bot 内部 API：X-Bot-Token 鉴权，不记操作日志（活动上报很频繁） ──

BotGroupInternalRouter = APIRouter(
    prefix="/internal/group",
    tags=["Bot内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@BotGroupInternalRouter.post("/sync", summary="同步 bot 所在的全部群组", response_model=ResponseSchema[list[BotGroupStateSchema]])
async def sync_groups_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotGroupSyncSchema, Body()],
) -> JSONResponse:
    result = await BotGroupService(AuthSchema(), db).sync(data=data)
    return SuccessResponse(data=result, msg="同步群组成功")


@BotGroupInternalRouter.post("/join", summary="bot 加入群组", response_model=ResponseSchema[BotGroupStateSchema])
async def join_group_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotGroupJoinSchema, Body()],
) -> JSONResponse:
    result = await BotGroupService(AuthSchema(), db).join(data=data)
    return SuccessResponse(data=result, msg="登记群组成功")


@BotGroupInternalRouter.post("/leave", summary="bot 离开群组", response_model=ResponseSchema[None])
async def leave_group_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotGroupRefSchema, Body()],
) -> JSONResponse:
    await BotGroupService(AuthSchema(), db).leave(data=data)
    return SuccessResponse(msg="已标记离开")


@BotGroupInternalRouter.post("/activity", summary="上报群组活动", response_model=ResponseSchema[bool])
async def group_activity_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BotGroupRefSchema, Body()],
) -> JSONResponse:
    found = await BotGroupService(AuthSchema(), db).touch(data=data)
    return SuccessResponse(data=found, msg="已更新活动时间" if found else "群组未登记")
