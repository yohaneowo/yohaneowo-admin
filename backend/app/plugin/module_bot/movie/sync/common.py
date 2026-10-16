"""Shared helpers for the sync: JavaScript-compatible rounding/conversion, and short-lived
database sessions for each outside call (a sync takes minutes; holding one transaction that long
would lock the usage rows the bot's own requests update)."""

import math
from typing import Any

from app.core.database import async_db_session

from .. import service
from ..sources import tmdb, trakt


def js_round(value: float) -> int:
    """Math.round: halves go up (Python's round() goes to even)."""
    return math.floor(value + 0.5)


def js_str(value: Any) -> str:
    """String(value) for the values the sync compares: null → "null", undefined is never stored."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def js_number(value: Any) -> float:
    """Number(value) for the cached year field: null → 0, "" → 0, junk → NaN."""
    if value is None or isinstance(value, bool):
        return float(bool(value))
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return math.nan


def is_number(value: Any) -> bool:
    """typeof value === 'number'."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_integer(value: Any) -> bool:
    """Number.isInteger(value)."""
    return is_number(value) and math.isfinite(value) and float(value).is_integer()


async def read_cache(namespace: str, keys: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(keys), 500):
        async with async_db_session() as db:
            found.update(await service.read_cache(db, namespace, keys[start : start + 500]))
    return found


async def write_cache(namespace: str, items: dict[str, dict]) -> None:
    if not items:
        return
    async with async_db_session() as db, db.begin():
        await service.write_cache(db, namespace, items)


async def tmdb_get(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    async with async_db_session() as db, db.begin():
        return await tmdb.get(db, path, params)


async def trakt_get(path: str, params: dict[str, Any] | None = None) -> tuple[int, int | None, Any]:
    async with async_db_session() as db, db.begin():
        return await trakt.get(db, path, params)
