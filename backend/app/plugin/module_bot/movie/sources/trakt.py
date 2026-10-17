"""Trakt: watch history and ratings, and the OAuth that lets a website account connect its Trakt
account. Public profiles only need the app's client ID; a connected account's token also reads a
private profile. The flow uses PKCE (Trakt requires it for apps created since 2026-10 and issues
them no client secret); when an older app does have a secret, it stays here (Doppler) and is sent
along too, the website never sees it."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings

from .base import SourceUnavailable, fetch, json_or_none

API_URL = "https://api.trakt.tv"
AUTHORIZE_URL = "https://trakt.tv/oauth/authorize"
ALLOWED_PATH = re.compile(r"/users/[^/?#\s]+(/(ratings|watched)/(movies|shows|seasons|episodes))?|/search/tmdb/\d+")
ALLOWED_PARAMS = {"page", "limit", "type", "extended"}


class TraktSettings(BaseSettings):
    model_config = Settings.model_config
    TRAKT_CLIENT_ID: str = ""
    TRAKT_CLIENT_SECRET: str = ""


def client_id() -> str:
    return TraktSettings().TRAKT_CLIENT_ID


class TraktAuthError(Exception):
    """The authorization code or refresh token was rejected (expired, reused, revoked)."""


@dataclass
class TraktTokens:
    access_token: str
    refresh_token: str
    # Naive UTC, like the rest of the movie tables.
    expires_at: datetime


def _headers(token: str | None = None) -> dict[str, str]:
    key = client_id()
    if not key:
        raise SourceUnavailable("admin 没有设定 TRAKT_CLIENT_ID")
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": key,
        "User-Agent": "yohaneowo-admin",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def get(db: AsyncSession, path: str, params: dict[str, Any] | None = None, token: str | None = None) -> tuple[int, int | None, Any]:
    """(HTTP status, X-Pagination-Page-Count or None, JSON body) of one Trakt GET, as the app or,
    with `token`, as the person who authorized it (private profiles answer then)."""
    query = {k: v for k, v in (params or {}).items() if k in ALLOWED_PARAMS}
    response = await fetch(db, "trakt", f"{API_URL}{path}", params=query, headers=_headers(token), timeout=20)
    pages = response.headers.get("x-pagination-page-count")
    return response.status_code, int(pages) if pages and pages.isdigit() else None, json_or_none(response)


async def call(db: AsyncSession, method: str, path: str, *, params: dict[str, Any] | None = None, token: str | None = None, json: Any = None) -> tuple[int, int | None, Any]:
    """One Trakt request of any method and path (internal callers only; `get` is the whitelisted
    proxy for the bot). Returns (status, page count, body) like `get`."""
    response = await fetch(db, "trakt", f"{API_URL}{path}", params=params, headers=_headers(token), timeout=30, method=method, json=json)
    pages = response.headers.get("x-pagination-page-count")
    return response.status_code, int(pages) if pages and pages.isdigit() else None, json_or_none(response)


def authorize_url(redirect_uri: str, state: str, code_challenge: str) -> str:
    """Where the browser goes to let this app read the person's Trakt account. `code_challenge` is
    the S256 hash of the verifier the website keeps for the token exchange."""
    key = client_id()
    if not key:
        raise SourceUnavailable("admin 没有设定 TRAKT_CLIENT_ID")
    query = {"response_type": "code", "client_id": key, "redirect_uri": redirect_uri, "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"}
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _tokens(body: Any) -> TraktTokens:
    if not isinstance(body, dict) or not body.get("access_token") or not body.get("refresh_token"):
        raise TraktAuthError("token response without tokens")
    created = int(body.get("created_at") or datetime.now(UTC).timestamp())
    expires_in = int(body.get("expires_in") or 0)
    expires_at = datetime.fromtimestamp(created + expires_in, UTC).replace(tzinfo=None)
    return TraktTokens(access_token=str(body["access_token"]), refresh_token=str(body["refresh_token"]), expires_at=expires_at)


async def _token_request(db: AsyncSession, payload: dict[str, str]) -> TraktTokens:
    settings = TraktSettings()
    if not settings.TRAKT_CLIENT_ID:
        raise SourceUnavailable("admin 没有设定 TRAKT_CLIENT_ID")
    body = {**payload, "client_id": settings.TRAKT_CLIENT_ID}
    # PKCE apps have no secret; Trakt would reject a wrong one, so only send a real, distinct value.
    if settings.TRAKT_CLIENT_SECRET and settings.TRAKT_CLIENT_SECRET != settings.TRAKT_CLIENT_ID:
        body["client_secret"] = settings.TRAKT_CLIENT_SECRET
    response = await fetch(db, "trakt", f"{API_URL}/oauth/token", method="POST", json=body, headers={"Content-Type": "application/json", "User-Agent": "yohaneowo-admin"}, timeout=20)
    if response.status_code in (400, 401, 403):
        raise TraktAuthError(f"Trakt token request rejected: HTTP {response.status_code}")
    if response.status_code != 200:
        raise SourceUnavailable(f"Trakt 暂时无法连接（HTTP {response.status_code}）")
    return _tokens(json_or_none(response))


async def exchange_code(db: AsyncSession, code: str, redirect_uri: str, code_verifier: str) -> TraktTokens:
    """The authorization code Trakt sent the browser back with → tokens. `code_verifier` is the
    PKCE verifier whose hash started the authorization."""
    return await _token_request(db, {"code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": code_verifier})


async def refresh(db: AsyncSession, refresh_token: str, redirect_uri: str) -> TraktTokens:
    """New tokens from a refresh token (each refresh token works once)."""
    return await _token_request(db, {"refresh_token": refresh_token, "redirect_uri": redirect_uri, "grant_type": "refresh_token"})


def expiring(tokens: TraktTokens, within: timedelta = timedelta(days=7)) -> bool:
    return tokens.expires_at <= datetime.now(UTC).replace(tzinfo=None) + within


async def profile(db: AsyncSession, slug: str, token: str | None = None) -> dict | None:
    """A Trakt user's public profile (username, name, avatar), or None when Trakt won't show it
    (unknown, private without the owner's token) or is unreachable; never fails a sync."""
    try:
        status, _, body = await get(db, f"/users/{slug}", {"extended": "full"}, token=token)
    except SourceUnavailable:
        return None
    return body if status == 200 and isinstance(body, dict) else None


async def me(db: AsyncSession, token: str) -> dict:
    """The authorized person's own profile (slug, username, avatar, private)."""
    status, _, body = await get(db, "/users/me", {"extended": "full"}, token=token)
    if status in (401, 403):
        raise TraktAuthError(f"Trakt /users/me answered HTTP {status}")
    if status != 200 or not isinstance(body, dict):
        raise SourceUnavailable(f"Trakt 暂时无法连接（HTTP {status}）")
    return body
