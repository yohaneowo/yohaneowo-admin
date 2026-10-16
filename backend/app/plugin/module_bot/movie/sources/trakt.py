"""Trakt: public users' watch history and ratings. Public profiles only need the app's client ID."""

import re
from typing import Any

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings

from .base import SourceUnavailable, fetch, json_or_none

API_URL = "https://api.trakt.tv"
ALLOWED_PATH = re.compile(r"/users/[^/?#\s]+(/(ratings|watched)/(movies|shows|seasons|episodes))?|/search/tmdb/\d+")
ALLOWED_PARAMS = {"page", "limit", "type", "extended"}


class TraktSettings(BaseSettings):
    model_config = Settings.model_config
    TRAKT_CLIENT_ID: str = ""


def client_id() -> str:
    return TraktSettings().TRAKT_CLIENT_ID


async def get(db: AsyncSession, path: str, params: dict[str, Any] | None = None) -> tuple[int, int | None, Any]:
    """(HTTP status, X-Pagination-Page-Count or None, JSON body) of one Trakt GET."""
    key = client_id()
    if not key:
        raise SourceUnavailable("admin 没有设定 TRAKT_CLIENT_ID")
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": key,
        "User-Agent": "yohaneowo-admin",
    }
    query = {k: v for k, v in (params or {}).items() if k in ALLOWED_PARAMS}
    response = await fetch(db, "trakt", f"{API_URL}{path}", params=query, headers=headers, timeout=20)
    pages = response.headers.get("x-pagination-page-count")
    return response.status_code, int(pages) if pages and pages.isdigit() else None, json_or_none(response)
