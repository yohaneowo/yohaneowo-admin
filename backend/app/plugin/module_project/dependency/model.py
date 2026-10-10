from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class ProjectDependencyModel(ModelMixin):
    """项目依赖：由项目自己（如 yohaneowo-bot）上报清单，admin 每次收到清单后检查上游

    清单以最新一次上报为准，没再出现的依赖会被删掉（连同检查记录）。
    不挂 UserMixin：数据由 bot 写入，没有创建人（同 BotGroupModel）。
    """

    __tablename__: str = "project_dependency"
    __table_args__ = (
        Index("ix_project_dependency_status_deleted", "status", "is_deleted"),
        Index("ix_project_dependency_created_deleted", "created_time", "is_deleted"),
        UniqueConstraint("project", "name", name="uq_project_dependency_project_name"),
        {"comment": "项目依赖表"},
    )

    project: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="项目，如 yohaneowo-bot")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="依赖名称")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="分类，由上报方决定，如 后端、前端、前端开发工具、Docker 镜像")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="来源类型(npm/pypi/github/docker/none)")
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="查询标识：npm/PyPI 包名、GitHub owner/repo、Docker 镜像")
    usage: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="用途说明")
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否重点依赖(由上报方标记，坏了影响最大)")
    installed_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="项目实际使用的版本(上报)")
    reported_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="最近一次上报时间")

    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="上游最新版本")
    upstream_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="上游最后更新时间")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="上游是否已封存(GitHub)")
    outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="实际版本是否落后于最新版本")
    major_behind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否落后一个大版本(升级可能有不兼容改动)")
    vulnerable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, comment="实际版本是否有已知漏洞(OSV)")
    vulnerabilities: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True, comment="已知漏洞 [{id, aliases, summary, severity, fixed}]")
    vuln_severity: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="已知漏洞的最高等级(CRITICAL/HIGH/MODERATE/LOW)")
    fix_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="修复全部已知漏洞要升级到的版本")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", comment="状态(healthy/warning/risk/unknown/error/skipped)")
    check_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次检查的错误")
    checked_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近一次检查时间")


class ProjectDependencyCheckModel(ModelMixin):
    """每次检查一条记录，用来看依赖从什么时候开始停止更新"""

    __tablename__: str = "project_dependency_check"
    __table_args__ = (
        Index("ix_project_dependency_check_status_deleted", "status", "is_deleted"),
        Index("ix_project_dependency_check_created_deleted", "created_time", "is_deleted"),
        {"comment": "项目依赖检查记录表"},
    )

    dependency_id: Mapped[int] = mapped_column(Integer, ForeignKey("project_dependency.id", ondelete="CASCADE"), nullable=False, index=True, comment="依赖ID")
    installed_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当时的实际版本")
    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当时的最新版本")
    upstream_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="当时查到的上游最后更新时间")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否已封存")
    vulnerability_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="当时的已知漏洞数")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="状态")
    check_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误")
    checked_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="检查时间")
