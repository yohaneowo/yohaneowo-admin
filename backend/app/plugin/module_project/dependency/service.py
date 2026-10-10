import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.database import async_db_session
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from .checker import UpstreamInfo, check_many, is_major_behind, is_outdated, status_of
from .crud import ProjectDependencyCRUD
from .model import ProjectDependencyCheckModel, ProjectDependencyModel
from .schema import (
    AttentionItemSchema,
    AttentionReasonSchema,
    DependencyReportSchema,
    ProjectDependencyCheckOutSchema,
    ProjectDependencyCheckResultSchema,
    ProjectDependencyDetailSchema,
    ProjectDependencyFiltersSchema,
    ProjectDependencyOutSchema,
    ProjectDependencyOverviewSchema,
    ProjectDependencyQueryParam,
)
from .self_report import PROJECT as SELF_PROJECT
from .self_report import collect as collect_self
from .vulns import check_vulnerabilities, fix_version, max_severity

# 有问题的排前面；skipped（kind=none，没有上游可查，如 ffmpeg、Node.js）排最后
STATUS_ORDER = {"risk": 0, "error": 1, "warning": 2, "unknown": 3, "healthy": 4, "skipped": 5}
DETAIL_CHECK_LIMIT = 30
LEVEL_ORDER = {"critical": 0, "high": 1, "medium": 2}
VULN_LEVEL = {"CRITICAL": "critical", "HIGH": "critical", "MODERATE": "high"}


def attention_reasons(dep: ProjectDependencyOutSchema) -> list[AttentionReasonSchema]:
    """总览「需要处理」的原因。非重点依赖久未更新只计入卡片不列出：成熟的小工具本来就很少更新"""
    reasons = []
    if dep.vulnerable:
        vulns = dep.vulnerabilities or []
        fix = f"升级到 {dep.fix_version} 可全部修复" if dep.fix_version else "部分漏洞尚无修复版本"
        reasons.append(AttentionReasonSchema(level=VULN_LEVEL.get(dep.vuln_severity or "", "medium"), text=f"已知漏洞 {len(vulns)} 个（最高 {dep.vuln_severity or '未分级'}），{fix}"))
    if dep.archived:
        reasons.append(AttentionReasonSchema(level="critical", text="上游项目已封存，不会再更新"))
    if dep.status == "error":
        reasons.append(AttentionReasonSchema(level="medium", text=f"检查失败：{(dep.check_error or '').splitlines()[0][:120]}"))
    if dep.major_behind:
        reasons.append(AttentionReasonSchema(level="medium", text=f"落后大版本：{dep.installed_version} → {dep.latest_version}，升级可能有不兼容改动"))
    if dep.critical:
        if dep.status == "risk" and not dep.archived:
            reasons.append(AttentionReasonSchema(level="high", text="重点依赖，上游超过一年没更新"))
        elif dep.status == "warning":
            reasons.append(AttentionReasonSchema(level="medium", text="重点依赖，上游半年以上没更新"))
        if dep.outdated and not dep.major_behind:
            reasons.append(AttentionReasonSchema(level="medium", text=f"重点依赖有新版本：{dep.installed_version} → {dep.latest_version}"))
    return reasons


def _list_order(item: ProjectDependencyOutSchema) -> tuple:
    """有漏洞的最前，再按状态由差到好；同状态里重点依赖、落后大版本、可更新的靠前"""
    return (not item.vulnerable, STATUS_ORDER.get(item.status, 9), not item.critical, not item.major_behind, not item.outdated, item.name.lower())


class ProjectDependencyService:
    """项目依赖：上报清单、检查上游、查询"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    # ── 后台 ──────────────────────────────────────────────────────

    async def filters(self) -> ProjectDependencyFiltersSchema:
        model = ProjectDependencyModel
        projects = await self.db.execute(select(distinct(model.project)).order_by(model.project))
        categories = await self.db.execute(select(distinct(model.category)).where(model.category.is_not(None)).order_by(model.category))
        return ProjectDependencyFiltersSchema(projects=list(projects.scalars().all()), categories=list(categories.scalars().all()))

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ProjectDependencyQueryParam | None = None,
    ) -> PageResultSchema[ProjectDependencyOutSchema]:
        # 依赖不多（每个项目几十个），整批取出后按状态排序再分页，避免在 SQL 里写状态顺序
        objs = await ProjectDependencyCRUD(self.auth, self.db).get_list(search=search_to_dict(search), order_by=[{"name": "asc"}])
        items = sorted((ProjectDependencyOutSchema.model_validate(o) for o in objs), key=_list_order)
        offset = (page_no - 1) * page_size
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=len(items),
            has_next=offset + page_size < len(items),
            items=items[offset : offset + page_size],
        )

    async def overview(self, project: str | None = None) -> ProjectDependencyOverviewSchema:
        sql = select(ProjectDependencyModel).where(ProjectDependencyModel.is_deleted.is_(False))
        if project:
            sql = sql.where(ProjectDependencyModel.project == project)
        deps = [ProjectDependencyOutSchema.model_validate(o) for o in (await self.db.execute(sql)).scalars().all()]

        items = []
        for dep in deps:
            if reasons := attention_reasons(dep):
                reasons.sort(key=lambda r: LEVEL_ORDER[r.level])
                items.append(AttentionItemSchema(dependency=dep, level=reasons[0].level, reasons=reasons))
        items.sort(key=lambda i: (LEVEL_ORDER[i.level], not i.dependency.critical, -len(i.reasons), i.dependency.name.lower()))

        checked = [d.checked_time for d in deps if d.checked_time]
        return ProjectDependencyOverviewSchema(
            total=len(deps),
            attention=len(items),
            vulnerable=sum(d.vulnerable for d in deps),
            vulnerability_count=sum(len(d.vulnerabilities or []) for d in deps),
            major_behind=sum(d.major_behind for d in deps),
            outdated=sum(d.outdated for d in deps),
            risk=sum(d.status == "risk" for d in deps),
            warning=sum(d.status == "warning" for d in deps),
            healthy=sum(d.status == "healthy" for d in deps),
            error=sum(d.status == "error" for d in deps),
            critical=sum(d.critical for d in deps),
            last_checked_time=max(checked) if checked else None,
            items=items,
        )

    async def detail(self, id: int) -> ProjectDependencyDetailSchema:
        obj = await ProjectDependencyCRUD(self.auth, self.db).get_or_404(id=id)
        rows = await self.db.execute(
            select(ProjectDependencyCheckModel).where(ProjectDependencyCheckModel.dependency_id == id).order_by(ProjectDependencyCheckModel.checked_time.desc()).limit(DETAIL_CHECK_LIMIT)
        )
        out = ProjectDependencyDetailSchema.model_validate(obj)
        out.checks = [ProjectDependencyCheckOutSchema.model_validate(c) for c in rows.scalars().all()]
        return out

    async def refresh_self(self) -> None:
        """admin 自己的清单不靠上报：检查前重新收集一次（版本以当前运行的后端与构建时的前端为准）"""
        await self.report(collect_self())

    async def check(self, project: str | None = None) -> ProjectDependencyCheckResultSchema:
        """查一遍上游并更新状态，每个依赖留一条检查记录。project 为空则全部项目"""
        if project in (None, SELF_PROJECT):
            await self.refresh_self()
        sql = select(ProjectDependencyModel).where(ProjectDependencyModel.is_deleted.is_(False))
        if project:
            sql = sql.where(ProjectDependencyModel.project == project)
        deps = list((await self.db.execute(sql)).scalars().all())
        # 上游与漏洞库同时查：两边各自失败不影响对方
        results, vuln_results = await asyncio.gather(
            check_many([(d.kind, d.source) for d in deps]),
            check_vulnerabilities([(d.kind, d.source, d.installed_version) for d in deps]),
        )

        now = datetime.now(UTC)
        failed = 0
        for dep, result, vulns in zip(deps, results, vuln_results, strict=True):
            if isinstance(result, Exception):
                # 查询失败保留上次查到的版本与时间，只标出错误
                failed += 1
                dep.status, dep.check_error = "error", f"{type(result).__name__}: {result}"[:2000]
            else:
                info: UpstreamInfo = result
                dep.latest_version = info.latest_version
                dep.upstream_updated_time = info.updated_time
                dep.archived = info.archived
                dep.status = status_of(info, now) if dep.kind != "none" else "skipped"
                dep.check_error = None
            dep.outdated = is_outdated(dep.installed_version, dep.latest_version)
            dep.major_behind = is_major_behind(dep.installed_version, dep.latest_version)
            if isinstance(vulns, Exception):
                # 漏洞库连不上：保留上次的漏洞资料，只加注错误
                note = f"漏洞库查询失败：{type(vulns).__name__}: {vulns}"[:500]
                dep.check_error = f"{dep.check_error}\n{note}" if dep.check_error else note
            else:
                dep.vulnerabilities = vulns or None
                dep.vulnerable = bool(vulns)
                dep.vuln_severity = max_severity([v.get("severity") for v in vulns])
                dep.fix_version = fix_version(vulns)
            dep.checked_time = now
            self.db.add(
                ProjectDependencyCheckModel(
                    dependency_id=dep.id,
                    installed_version=dep.installed_version,
                    latest_version=dep.latest_version,
                    upstream_updated_time=dep.upstream_updated_time,
                    archived=dep.archived,
                    vulnerability_count=len(dep.vulnerabilities or []),
                    status=dep.status,
                    check_error=dep.check_error,
                    checked_time=now,
                )
            )
        await self.db.flush()
        return ProjectDependencyCheckResultSchema(checked=len(deps), failed=failed)

    # ── 项目上报 ──────────────────────────────────────────────────

    async def report(self, data: DependencyReportSchema) -> int:
        """以本次清单为准：新增或更新，清单里没有的旧依赖删除（检查记录随外键一起删）"""
        now = datetime.now(UTC)
        existing = {d.name: d for d in (await self.db.execute(select(ProjectDependencyModel).where(ProjectDependencyModel.project == data.project))).scalars().all()}
        reported = {item.name: item for item in data.dependencies}
        for name, item in reported.items():
            dep = existing.get(name)
            if dep is None:
                dep = ProjectDependencyModel(project=data.project, name=name, kind=item.kind, source=item.source, reported_time=now)
                self.db.add(dep)
            elif (dep.kind, dep.source) != (item.kind, item.source):
                # 来源换了，上次查到的上游资料不再适用
                dep.kind, dep.source = item.kind, item.source
                dep.latest_version = dep.upstream_updated_time = dep.checked_time = dep.check_error = None
                dep.archived, dep.status = False, "unknown"
            dep.category = item.category
            dep.usage = item.usage
            dep.critical = item.critical
            if dep.installed_version != item.installed_version:
                # 换了版本，上次查到的漏洞不一定还适用，等下次检查再填
                dep.vulnerabilities, dep.vulnerable, dep.vuln_severity, dep.fix_version = None, False, None, None
            dep.installed_version = item.installed_version
            dep.reported_time = now
            dep.outdated = is_outdated(dep.installed_version, dep.latest_version)
            dep.major_behind = is_major_behind(dep.installed_version, dep.latest_version)

        removed = [d.id for name, d in existing.items() if name not in reported]
        if removed:
            await self.db.execute(delete(ProjectDependencyCheckModel).where(ProjectDependencyCheckModel.dependency_id.in_(removed)))
            await self.db.execute(delete(ProjectDependencyModel).where(ProjectDependencyModel.id.in_(removed)))
        await self.db.flush()
        return len(reported)


async def check_in_background(reported_project: str) -> None:
    """上报的请求已经回应后再查上游（要几秒到几十秒），用独立的数据库会话。

    bot 每天上报一次，admin 没有自己的排程，所以这时把所有项目（含 admin 自己）一起检查。
    """
    try:
        async with async_db_session() as db, db.begin():
            result = await ProjectDependencyService(AuthSchema(), db).check()
        logger.info("{} 上报后依赖检查完成：{} 个，失败 {} 个", reported_project, result.checked, result.failed)
    except Exception:
        logger.exception("{} 上报后依赖检查失败", reported_project)
