from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.database import async_db_session
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute
from app.plugin.module_bot.internal_auth import verify_bot_token

from .schema import (
    DependencyReportSchema,
    ProjectDependencyCheckResultSchema,
    ProjectDependencyDetailSchema,
    ProjectDependencyFiltersSchema,
    ProjectDependencyOutSchema,
    ProjectDependencyOverviewSchema,
    ProjectDependencyQueryParam,
)
from .service import ProjectDependencyService, check_in_background

# 只读查询，不挂 OperationLogRoute；手动检查会改数据，单独挂在下面的路由上记操作日志
ProjectDependencyRouter = APIRouter(prefix="/dependency", tags=["项目依赖"])
ProjectDependencyActionRouter = APIRouter(route_class=OperationLogRoute, prefix="/dependency", tags=["项目依赖"])


@ProjectDependencyRouter.get("/filters", summary="搜索栏选项（项目、分类）", response_model=ResponseSchema[ProjectDependencyFiltersSchema])
async def get_dependency_filters_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_project:dependency:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ProjectDependencyService(auth, db).filters()
    return SuccessResponse(data=result, msg="查询筛选选项成功")


@ProjectDependencyRouter.get("/overview", summary="依赖总览：统计与需要处理的清单", response_model=ResponseSchema[ProjectDependencyOverviewSchema])
async def get_dependency_overview_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_project:dependency:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    project: Annotated[str | None, Query(description="只看这个项目；不传则全部")] = None,
) -> JSONResponse:
    result = await ProjectDependencyService(auth, db).overview(project)
    return SuccessResponse(data=result, msg="查询依赖总览成功")


@ProjectDependencyRouter.get("/list", summary="查询项目依赖", response_model=ResponseSchema[PageResultSchema[ProjectDependencyOutSchema]])
async def get_dependency_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_project:dependency:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ProjectDependencyQueryParam, Query()],
) -> JSONResponse:
    result = await ProjectDependencyService(auth, db).page(page_no=page.page_no, page_size=page.page_size, search=search)
    return SuccessResponse(data=result, msg="查询项目依赖成功")


@ProjectDependencyRouter.get("/detail/{id}", summary="项目依赖详情（含检查记录）", response_model=ResponseSchema[ProjectDependencyDetailSchema])
async def get_dependency_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_project:dependency:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="依赖ID", ge=1)],
) -> JSONResponse:
    result = await ProjectDependencyService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取项目依赖详情成功")


@ProjectDependencyActionRouter.post("/check", summary="立即检查上游", response_model=ResponseSchema[ProjectDependencyCheckResultSchema])
async def check_dependency_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_project:dependency:check"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    project: Annotated[str | None, Query(description="只检查这个项目；不传则全部")] = None,
) -> JSONResponse:
    result = await ProjectDependencyService(auth, db).check(project)
    return SuccessResponse(data=result, msg=f"检查完成：{result.checked} 个，失败 {result.failed} 个")


# ── 项目上报：与 bot 共用 X-Bot-Token（BOT_API_TOKEN） ──

ProjectDependencyInternalRouter = APIRouter(
    prefix="/internal/dependency",
    tags=["Bot内部接口"],
    dependencies=[Depends(verify_bot_token)],
)


@ProjectDependencyInternalRouter.post("/report", summary="上报项目的依赖清单", response_model=ResponseSchema[int])
async def report_dependency_controller(
    data: Annotated[DependencyReportSchema, Body()],
    background: BackgroundTasks,
) -> JSONResponse:
    # 不用 db_getter：它的事务要等后台任务跑完才提交，后台检查会读不到刚写入的清单。
    # 这里自己开会话、写完立刻提交，回应后再在后台查上游
    async with async_db_session() as db, db.begin():
        count = await ProjectDependencyService(AuthSchema(), db).report(data)
    background.add_task(check_in_background, data.project)
    return SuccessResponse(data=count, msg="上报依赖清单成功")
