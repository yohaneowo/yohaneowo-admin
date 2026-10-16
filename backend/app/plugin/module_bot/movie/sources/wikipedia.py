"""Wikidata / English Wikipedia API reads, for the special-format sentences (IMAX, 4DX…).

Wikimedia answers "too many requests" to fast clients, so requests are serialized here, GAP_SECONDS
apart, and a rate-limited answer (plain text instead of JSON) is retried once after a pause.
"""

import asyncio
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .base import SourceUnavailable, fetch, json_or_none

ALLOWED_URL = re.compile(r"https://(www\.wikidata\.org|en\.wikipedia\.org)/w/api\.php\?[^#\s]+")
USER_AGENT = "yohaneowo-admin/1.0 (https://github.com/yohaneowo)"
GAP_SECONDS = 0.4
RATE_LIMIT_WAIT_SECONDS = 5
_queue = asyncio.Lock()


async def get(db: AsyncSession, url: str) -> tuple[int, Any]:
    async with _queue:
        for attempt in (1, 2):
            response = await fetch(db, "wikipedia", url, headers={"User-Agent": USER_AGENT})
            await asyncio.sleep(GAP_SECONDS)
            body = json_or_none(response)
            if body is not None:
                return response.status_code, body
            if attempt == 2:
                raise SourceUnavailable(f"Wikimedia 回应 HTTP {response.status_code}，不是 JSON")
            await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
    raise AssertionError("unreachable")
