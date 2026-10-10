"""用 OSV.dev（Google 维护的开源漏洞数据库）查实际安装的版本有没有已知漏洞。

一次 querybatch 查完所有依赖（只回漏洞编号），再并发取每个漏洞的详情（摘要、等级、修复版本）。
同一个漏洞常有两个编号（GitHub 的 GHSA 与 PyPI 的 PYSEC 互为 alias），合并成一笔再计数。
"""

import asyncio

import httpx

from .checker import version_key

OSV_API = "https://api.osv.dev/v1"
ECOSYSTEMS = {"npm": "npm", "pypi": "PyPI"}
SEVERITY_ORDER = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
MAX_DETAIL_CONCURRENCY = 8
REQUEST_TIMEOUT = 30


def supported(kind: str, installed: str | None) -> bool:
    return kind in ECOSYSTEMS and bool(installed)


def max_severity(severities: list[str | None]) -> str | None:
    known = [s for s in severities if s in SEVERITY_ORDER]
    return max(known, key=SEVERITY_ORDER.index) if known else None


def _fixed_versions(vuln: dict, name: str, ecosystem: str) -> list[str]:
    return [
        event["fixed"]
        for affected in vuln.get("affected", [])
        if affected.get("package", {}).get("ecosystem") == ecosystem and affected["package"].get("name", "").lower() == name.lower()
        for rng in affected.get("ranges", [])
        for event in rng.get("events", [])
        if event.get("fixed")
    ]


def _merge_aliases(ids: list[str], details: dict[str, dict]) -> list[str]:
    """同一个漏洞只留一个编号：优先 GHSA（有等级与摘要），其次按出现顺序"""
    kept: list[str] = []
    seen: set[str] = set()
    for vid in sorted(ids, key=lambda v: (not v.startswith("GHSA-"), ids.index(v))):
        group = {vid, *details.get(vid, {}).get("aliases", [])}
        if group & seen:
            continue
        seen |= group
        kept.append(vid)
    return kept


async def check_vulnerabilities(items: list[tuple[str, str, str | None]]) -> list[list[dict] | Exception]:
    """items: [(kind, source, installed_version)]；不支持的项目回空列表。

    每个漏洞：{id, aliases, summary, severity, fixed}，fixed 是这个包的修复版本（可能为空：尚无修复）。
    """
    indexes = [i for i, (kind, _, version) in enumerate(items) if supported(kind, version)]
    results: list[list[dict] | Exception] = [[] for _ in items]
    if not indexes:
        return results

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"User-Agent": "yohaneowo-admin"}) as client:
        queries = [{"package": {"name": items[i][1], "ecosystem": ECOSYSTEMS[items[i][0]]}, "version": items[i][2]} for i in indexes]
        try:
            resp = await client.post(f"{OSV_API}/querybatch", json={"queries": queries})
            resp.raise_for_status()
            batch = resp.json()["results"]
        except Exception as e:  # noqa: BLE001 — 漏洞库连不上时整批标为失败，不影响上游检查
            for i in indexes:
                results[i] = e
            return results

        ids_per_item = {i: [v["id"] for v in res.get("vulns", [])] for i, res in zip(indexes, batch, strict=True)}
        all_ids = sorted({vid for ids in ids_per_item.values() for vid in ids})
        semaphore = asyncio.Semaphore(MAX_DETAIL_CONCURRENCY)

        async def detail(vid: str) -> tuple[str, dict]:
            async with semaphore:
                try:
                    r = await client.get(f"{OSV_API}/vulns/{vid}")
                    r.raise_for_status()
                    return vid, r.json()
                except Exception:  # noqa: BLE001 — 拿不到详情时只留编号
                    return vid, {}

        details = dict(await asyncio.gather(*(detail(v) for v in all_ids)))

    for i, ids in ids_per_item.items():
        kind, name, _ = items[i]
        vulns = []
        for vid in _merge_aliases(ids, details):
            d = details.get(vid, {})
            fixed = _fixed_versions(d, name, ECOSYSTEMS[kind])
            vulns.append(
                {
                    "id": vid,
                    "aliases": [a for a in d.get("aliases", []) if a != vid],
                    "summary": (d.get("summary") or "")[:300],
                    "severity": (d.get("database_specific") or {}).get("severity"),
                    "fixed": max(fixed, key=version_key) if fixed else None,
                }
            )
        results[i] = vulns
    return results


def fix_version(vulns: list[dict]) -> str | None:
    """修复全部漏洞要升级到的版本（各漏洞修复版本中最高的）；有漏洞尚无修复时回 None"""
    if not vulns or any(not v.get("fixed") for v in vulns):
        return None
    return max((v["fixed"] for v in vulns), key=version_key)
