from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

from .checker import source_url

DependencyKind = Literal["npm", "pypi", "github", "docker", "none"]

# ── 后台 ──────────────────────────────────────────────────────────


class ProjectDependencyOutSchema(BaseSchema):
    """项目依赖响应模型"""

    model_config = ConfigDict(from_attributes=True)

    project: str
    name: str
    category: str | None = None
    kind: str
    source: str
    usage: str | None = None
    critical: bool = False
    installed_version: str | None = None
    reported_time: DateTimeStr
    latest_version: str | None = None
    upstream_updated_time: DateTimeStr | None = None
    archived: bool
    outdated: bool
    major_behind: bool = False
    vulnerable: bool = False
    vulnerabilities: list[dict] | None = None
    vuln_severity: str | None = None
    fix_version: str | None = None
    status: str
    check_error: str | None = None
    checked_time: DateTimeStr | None = None

    @computed_field
    @property
    def source_url(self) -> str | None:
        return source_url(self.kind, self.source)


class ProjectDependencyCheckOutSchema(BaseSchema):
    """单次检查记录"""

    model_config = ConfigDict(from_attributes=True)

    installed_version: str | None = None
    latest_version: str | None = None
    upstream_updated_time: DateTimeStr | None = None
    archived: bool
    vulnerability_count: int = 0
    status: str
    check_error: str | None = None
    checked_time: DateTimeStr


class ProjectDependencyDetailSchema(ProjectDependencyOutSchema):
    checks: list[ProjectDependencyCheckOutSchema] = Field(default_factory=list, description="最近的检查记录（新的在前）")


class ProjectDependencyQueryParam(BaseModel):
    """项目依赖查询参数"""

    project: str | None = Field(None, description="项目", json_schema_extra={"q": "eq"})
    name: str | None = Field(None, description="依赖名称", json_schema_extra={"q": "like"})
    category: str | None = Field(None, description="分类", json_schema_extra={"q": "eq"})
    kind: str | None = Field(None, description="来源类型", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="状态", json_schema_extra={"q": "eq"})
    outdated: bool | None = Field(None, description="是否落后", json_schema_extra={"q": "eq"})
    critical: bool | None = Field(None, description="是否重点依赖", json_schema_extra={"q": "eq"})
    vulnerable: bool | None = Field(None, description="是否有已知漏洞", json_schema_extra={"q": "eq"})
    major_behind: bool | None = Field(None, description="是否落后大版本", json_schema_extra={"q": "eq"})


class ProjectDependencyFiltersSchema(BaseModel):
    """搜索栏下拉选项"""

    projects: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class AttentionReasonSchema(BaseModel):
    level: str = Field(..., description="严重程度 critical/high/medium")
    text: str


class AttentionItemSchema(BaseModel):
    dependency: ProjectDependencyOutSchema
    level: str = Field(..., description="最严重的原因的等级")
    reasons: list[AttentionReasonSchema]


class ProjectDependencyOverviewSchema(BaseModel):
    """依赖总览：统计卡片 + 需要处理的清单"""

    total: int
    attention: int = Field(..., description="需要处理的依赖数（即 items 的长度）")
    vulnerable: int = Field(..., description="有已知漏洞的依赖数")
    vulnerability_count: int = Field(..., description="已知漏洞总数（合并同一漏洞的不同编号后）")
    major_behind: int
    outdated: int
    risk: int = Field(..., description="超过一年没更新或已封存")
    warning: int
    healthy: int
    error: int
    critical: int = Field(..., description="重点依赖数")
    last_checked_time: DateTimeStr | None = None
    items: list[AttentionItemSchema]


class ProjectDependencyCheckResultSchema(BaseModel):
    checked: int = Field(..., description="检查的依赖数")
    failed: int = Field(..., description="检查失败的依赖数")


# ── 项目上报（内部 API）──────────────────────────────────────────


class DependencyReportItemSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=32, description="分类，如 后端、前端开发工具")
    kind: DependencyKind
    source: str = Field(default="", max_length=256, description="查询标识；kind=none 时可留空")
    usage: str | None = Field(default=None, max_length=255)
    critical: bool = Field(default=False, description="重点依赖：坏了影响最大，总览会特别提醒")
    installed_version: str | None = Field(default=None, max_length=64)


class DependencyReportSchema(BaseModel):
    """项目的完整依赖清单：没出现在清单里的旧依赖会被删除"""

    project: str = Field(..., min_length=1, max_length=64, pattern=r"^[\w.\-]+$")
    dependencies: list[DependencyReportItemSchema] = Field(..., max_length=500)
