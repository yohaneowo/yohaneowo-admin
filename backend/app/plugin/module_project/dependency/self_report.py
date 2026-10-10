"""yohaneowo-admin 自己的依赖清单：admin 就是自己，不靠上报，由后端直接收集。

- 后端：pyproject.toml 的直接依赖（含 mysql 驱动），版本取当前 Python 环境实际安装的
- 前端：package.json 的 dependencies / devDependencies，版本取 pnpm-lock.yaml 锁定的。
  开发时读仓库里的 frontend/web；镜像里没有前端源码，读 publish.sh 构建时复制进来的 frontend-manifest/
- 镜像与上游框架：self_dependencies.json；重点依赖的名单也在那里（critical）
"""

import json
import re
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml

from app.config.path_conf import BASE_DIR

from .schema import DependencyReportItemSchema, DependencyReportSchema

PROJECT = "yohaneowo-admin"
# 只收 mysql 驱动：NAS 与本机都用 MySQL，其它数据库驱动不会安装
EXTRAS = ("mysql",)
FRONTEND_DIRS = (BASE_DIR / "frontend-manifest", BASE_DIR.parent / "frontend" / "web")
STATIC_FILE = Path(__file__).with_name("self_dependencies.json")


def _requirement_name(requirement: str) -> str:
    """'uvicorn[standard]>=0.30; python_version>"3"' -> 'uvicorn'"""
    return re.split(r"[\[<>=~!; ]", requirement.strip(), maxsplit=1)[0]


def _installed(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _backend(pyproject: dict) -> list[DependencyReportItemSchema]:
    project = pyproject["project"]
    requirements = list(project.get("dependencies", []))
    for extra in EXTRAS:
        requirements += project.get("optional-dependencies", {}).get(extra, [])
    names = dict.fromkeys(_requirement_name(r) for r in requirements)
    return [DependencyReportItemSchema(name=n, category="后端", kind="pypi", source=n, installed_version=_installed(n)) for n in names]


def _locked_versions(lock_file: Path) -> dict[str, str]:
    """pnpm-lock.yaml 里根项目的锁定版本；'3.5.13(typescript@5.8.3)' 去掉括号里的 peer 依赖"""
    if not lock_file.exists():
        return {}
    root = (yaml.safe_load(lock_file.read_text(encoding="utf-8")) or {}).get("importers", {}).get(".", {})
    locked = {}
    for section in ("dependencies", "devDependencies"):
        for name, info in (root.get(section) or {}).items():
            raw = info.get("version", "") if isinstance(info, dict) else str(info)
            locked[name] = raw.split("(", 1)[0]
    return locked


def _frontend() -> list[DependencyReportItemSchema]:
    folder = next((d for d in FRONTEND_DIRS if (d / "package.json").exists()), None)
    if folder is None:
        return []
    pkg = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    locked = _locked_versions(folder / "pnpm-lock.yaml")
    items = []
    for section, category in (("dependencies", "前端"), ("devDependencies", "前端开发工具")):
        for name in pkg.get(section, {}):
            items.append(DependencyReportItemSchema(name=name, category=category, kind="npm", source=name, installed_version=locked.get(name)))
    return items


def collect() -> DependencyReportSchema:
    pyproject = tomllib.loads((BASE_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    static = json.loads(STATIC_FILE.read_text(encoding="utf-8"))
    external = []
    for item in static["external"]:
        installed = pyproject["project"]["version"] if item.pop("installed_version_from", None) == "pyproject" else None
        external.append(DependencyReportItemSchema(**item, installed_version=installed))
    packages = [*_backend(pyproject), *_frontend()]
    critical = set(static.get("critical", []))
    for item in packages:
        item.critical = item.name in critical
    return DependencyReportSchema(project=PROJECT, dependencies=[*packages, *external])
