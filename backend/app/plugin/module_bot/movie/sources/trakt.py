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
# A profile link as the Trakt app shares it (https://trakt.tv/users/<slug>, also app./www.), or a bare username.
PROFILE_URL = re.compile(r"^(?:https?://)?(?:app\.|www\.)?trakt\.tv/users/([^/?#\s]+)", re.IGNORECASE)
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


class TraktUserError(ValueError):
    """The Trakt account a person typed can't be used; `code` names the message (traktInvalid /
    traktNotFound / traktPrivate) so the website can show it in its own language."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_user(text: str) -> str | None:
    """The profile slug from a link or username; None when the text is neither."""
    value = text.strip().rstrip("/")
    match = PROFILE_URL.match(value)
    if match:
        value = match.group(1)
    value = value.lstrip("@")
    return value if USERNAME.fullmatch(value) else None


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


async def resolve_user(db: AsyncSession, text: str) -> tuple[str, str]:
    """(slug, username) of the public Trakt profile behind what a person typed."""
    slug = parse_user(text)
    if slug is None:
        raise TraktUserError("traktInvalid")
    status, _, body = await get(db, f"/users/{slug}")
    if status == 404:
        raise TraktUserError("traktNotFound")
    if status == 403 or (status == 200 and isinstance(body, dict) and body.get("private")):
        raise TraktUserError("traktPrivate")
    if status != 200 or not isinstance(body, dict):
        raise SourceUnavailable("Trakt 暂时无法连接")
    ids = body.get("ids") if isinstance(body.get("ids"), dict) else {}
    return str(ids.get("slug") or slug), str(body.get("username") or slug)
