"""到各个公开注册表查依赖的上游状态：最新版本、最后更新时间、是否封存。

都是不需要账号的公开 API。GitHub 未登录每小时 60 次，每天查一次足够；
依赖多了可在环境变量设 GITHUB_TOKEN 提高上限。
"""

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx

REQUEST_TIMEOUT = 20
# 上游多久没更新算「注意」/「风险」
WARNING_AFTER = timedelta(days=180)
RISK_AFTER = timedelta(days=365)
# 同时查询的数量，免得对注册表一次发太多请求
MAX_CONCURRENCY = 4


@dataclass
class UpstreamInfo:
    latest_version: str | None = None
    updated_time: datetime | None = None
    archived: bool = False


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs) -> dict | None:
    """404 返回 None（例如 GitHub 项目没有 release），其它错误抛出"""
    resp = await client.get(url, **kwargs)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _check_npm(client: httpx.AsyncClient, name: str) -> UpstreamInfo:
    # 用 deps.dev（Google 的开源依赖数据）：npm 官方的搜索 API 一次查一百多个包会被 429 限流，
    # 包的完整元数据又动辄几 MB。deps.dev 有最新版本（isDefault）与发布时间，一般几十 KB
    data = await _get_json(client, f"https://api.deps.dev/v3/systems/npm/packages/{quote(name, safe='')}")
    latest = next((v for v in (data or {}).get("versions", []) if v.get("isDefault")), None)
    if latest:
        return UpstreamInfo(latest_version=latest["versionKey"]["version"], updated_time=_parse_time(latest.get("publishedAt")))
    # deps.dev 还没收录（刚发布的新包）才拿 npm 的精简元数据：几 MB，modified 即最后一次发布的时间
    data = await _get_json(client, f"https://registry.npmjs.org/{name}", headers={"Accept": "application/vnd.npm.install-v1+json"})
    if data is None:
        raise ValueError(f"npm 上找不到 {name}")
    return UpstreamInfo(latest_version=data.get("dist-tags", {}).get("latest"), updated_time=_parse_time(data.get("modified")))


async def _check_pypi(client: httpx.AsyncClient, name: str) -> UpstreamInfo:
    data = await _get_json(client, f"https://pypi.org/pypi/{name}/json")
    if data is None:
        raise ValueError(f"PyPI 上找不到 {name}")
    # urls 是最新版本的各个文件，取最晚的上传时间
    uploads = [t for f in data.get("urls") or [] if (t := _parse_time(f.get("upload_time_iso_8601")))]
    return UpstreamInfo(latest_version=data.get("info", {}).get("version"), updated_time=max(uploads, default=None))


async def _check_github(client: httpx.AsyncClient, repo: str) -> UpstreamInfo:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    data = await _get_json(client, f"https://api.github.com/repos/{repo}", headers=headers)
    if data is None:
        raise ValueError(f"GitHub 上找不到 {repo}（可能已删除或改名）")
    release = await _get_json(client, f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
    return UpstreamInfo(
        latest_version=release.get("tag_name") if release else None,
        # 有 release 以发布时间为准；没有就看最后一次推送
        updated_time=_parse_time(release.get("published_at")) if release else _parse_time(data.get("pushed_at")),
        archived=bool(data.get("archived")),
    )


async def _check_docker(client: httpx.AsyncClient, image: str) -> UpstreamInfo:
    repo, _, tag = image.partition(":")
    tag = tag or "latest"
    if "/" not in repo:
        repo = f"library/{repo}"
    data = await _get_json(client, f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}")
    if data is None:
        raise ValueError(f"Docker Hub 上找不到 {repo}:{tag}")
    return UpstreamInfo(latest_version=tag, updated_time=_parse_time(data.get("last_updated")))


CHECKERS = {"npm": _check_npm, "pypi": _check_pypi, "github": _check_github, "docker": _check_docker}


def source_url(kind: str, source: str) -> str | None:
    """依赖在注册表上的网页，前端用来做链接"""
    if kind == "npm":
        return f"https://www.npmjs.com/package/{source}"
    if kind == "pypi":
        return f"https://pypi.org/project/{source}/"
    if kind == "github":
        return f"https://github.com/{source}"
    if kind == "docker":
        repo = source.partition(":")[0]
        return f"https://hub.docker.com/_/{repo}" if "/" not in repo else f"https://hub.docker.com/r/{repo}"
    return None


def version_key(version: str) -> tuple:
    """比较版本是否相同：去掉 v 前缀，数字段转成整数（PyPI 会把 2026.09.30 写成 2026.9.30）"""
    parts = re.split(r"[.\-+]", version.strip().lstrip("vV^~="))
    return tuple(int(p) if p.isdigit() else p for p in parts if p)


def is_outdated(installed: str | None, latest: str | None) -> bool:
    """实际版本比最新版本旧才算落后（测试版装得比正式发布还新时不算）"""
    if not installed or not latest or latest == "latest":
        return False
    mine, newest = version_key(installed), version_key(latest)
    try:
        return mine < newest
    except TypeError:
        # 版本格式对不上（数字段和文字段混着比），退回成「不一样就算落后」
        return mine != newest


def is_major_behind(installed: str | None, latest: str | None) -> bool:
    """落后一个大版本：主版本号较小；0.x 的包按语义化版本，次版本号变动就算不兼容"""
    if not is_outdated(installed, latest):
        return False
    mine, newest = version_key(installed or ""), version_key(latest or "")
    try:
        # 日历版本（yt-dlp 的 2026.8.19）跨年只是例行发布，不是不兼容的大版本
        if isinstance(newest[0], int) and newest[0] >= 1900:
            return False
        if mine[0] != newest[0]:
            return mine[0] < newest[0]
        return mine[0] == 0 and len(mine) > 1 and len(newest) > 1 and mine[1] < newest[1]
    except (IndexError, TypeError):
        return False


def status_of(info: UpstreamInfo, now: datetime) -> str:
    if info.archived:
        return "risk"
    if info.updated_time is None:
        return "unknown"
    age = now - info.updated_time
    if age > RISK_AFTER:
        return "risk"
    if age > WARNING_AFTER:
        return "warning"
    return "healthy"


async def check_many(items: list[tuple[str, str]]) -> list[UpstreamInfo | Exception]:
    """items: [(kind, source)]，结果顺序与输入一致；单个失败以 Exception 返回，不影响其它"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True, headers={"User-Agent": "yohaneowo-admin"}) as client:

        async def one(kind: str, source: str) -> UpstreamInfo | Exception:
            checker = CHECKERS.get(kind)
            if checker is None:
                return UpstreamInfo()
            async with semaphore:
                try:
                    return await checker(client, source)
                except Exception as e:  # noqa: BLE001 — 记录到该依赖上，继续查其它
                    return e

        return await asyncio.gather(*(one(kind, source) for kind, source in items))
