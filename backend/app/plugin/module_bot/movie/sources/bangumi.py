"""Bangumi (bgm.tv, no key): Chinese titles, scores and staff of anime entries.

Bangumi asks clients to identify themselves with a User-Agent; requests are serialized GAP_SECONDS
apart to stay polite.
"""

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .base import SourceUnavailable, fetch, json_or_none

API_URL = "https://api.bgm.tv"
USER_AGENT = "yohaneowo-admin/1.0 (https://github.com/yohaneowo)"
GAP_SECONDS = 0.5
ANIME = 2
_queue = asyncio.Lock()


async def _request(db: AsyncSession, path: str, *, method: str = "GET", params: dict | None = None, json: Any = None) -> Any:
    async with _queue:
        response = await fetch(db, "bangumi", f"{API_URL}{path}", method=method, params=params, json=json, headers={"User-Agent": USER_AGENT})
        await asyncio.sleep(GAP_SECONDS)
    if response.status_code == 404:
        return None
    body = json_or_none(response)
    if not 200 <= response.status_code < 300 or body is None:
        raise SourceUnavailable(f"Bangumi 回应 HTTP {response.status_code}")
    return body


async def search(db: AsyncSession, keyword: str, date_from: str, date_to: str) -> list[dict]:
    """Anime entries matching the keyword that first aired between the two dates (YYYY-MM-DD, inclusive)."""
    body = await _request(
        db,
        "/v0/search/subjects",
        method="POST",
        params={"limit": 10},
        json={"keyword": keyword, "filter": {"type": [ANIME], "air_date": [f">={date_from}", f"<={date_to}"]}},
    )
    return (body or {}).get("data") or []


async def person(db: AsyncSession, person_id: int) -> dict | None:
    """A person or company (Bangumi lists studios as persons): name, summary, images…"""
    body = await _request(db, f"/v0/persons/{person_id}")
    return body if isinstance(body, dict) else None


async def person_image(db: AsyncSession, person_id: int) -> str | None:
    """A person's photo, or a company's logo; None without one."""
    url = ((await person(db, person_id) or {}).get("images") or {}).get("large")
    return url if isinstance(url, str) and url else None


async def person_subjects(db: AsyncSession, person_id: int) -> list[dict]:
    """Every entry the person or company worked on: [{id, name, name_cn, type, staff (the relation)}]."""
    body = await _request(db, f"/v0/persons/{person_id}/subjects")
    return body if isinstance(body, list) else []


async def person_characters(db: AsyncSession, person_id: int) -> list[dict]:
    """A voice actor's roles: [{id, name (the character), staff (主角 / 配角…), subject_id,
    subject_type, subject_name, subject_name_cn}]."""
    body = await _request(db, f"/v0/persons/{person_id}/characters")
    return body if isinstance(body, list) else []


async def search_persons(db: AsyncSession, keyword: str) -> list[dict]:
    """People and companies matching the keyword: [{id, name, …}]."""
    body = await _request(db, "/v0/search/persons", method="POST", params={"limit": 10}, json={"keyword": keyword})
    return (body or {}).get("data") or []


async def subject(db: AsyncSession, subject_id: int) -> dict | None:
    """One entry: name_cn, date, rating {score, total}…"""
    body = await _request(db, f"/v0/subjects/{subject_id}")
    return body if isinstance(body, dict) else None


async def persons(db: AsyncSession, subject_id: int) -> list[dict]:
    """Staff and companies of an entry: [{id, name, relation (e.g. 导演), type (1 person, 2 company)}]."""
    body = await _request(db, f"/v0/subjects/{subject_id}/persons")
    return body if isinstance(body, list) else []
