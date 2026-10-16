"""One request to a third-party API, checked against and counted in 第三方接口 (module_external)."""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.plugin.module_external import service as usage


class SourceUnavailable(Exception):
    """Not configured, out of quota for today, or unreachable. The message is safe to show."""


async def fetch(
    db: AsyncSession,
    provider: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    method: str = "GET",
    json: Any = None,
) -> httpx.Response:
    """A request (GET unless method says otherwise) with quota check and usage accounting. 404s and
    other client answers are returned, not raised."""
    if await usage.remaining(db, provider) == 0:
        raise SourceUnavailable(f"{usage.label(provider)} 今天的额度已用完")
    try:
        # Redirects are followed: MyAnimeList answers merged entries with a 307 to the new ID.
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(method, url, params=params, headers=headers, json=json)
    except httpx.HTTPError as error:
        await usage.record(db, provider, requests=1, failures=1, error=f"{type(error).__name__}: {url.split('?')[0]}")
        raise SourceUnavailable(f"{usage.label(provider)} 暂时连不上") from error
    failed = response.status_code >= 500 or response.status_code in (401, 403, 429)
    await usage.record(
        db,
        provider,
        requests=1,
        failures=int(failed),
        error=f"HTTP {response.status_code}: {url.split('?')[0]}" if failed else None,
    )
    return response


def json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
