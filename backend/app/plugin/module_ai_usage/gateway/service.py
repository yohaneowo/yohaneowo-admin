"""AI 转发：各项目把请求交给 admin，admin 用自己保管的 key 调用 Claude / Voyage，并直接写进 AI 调用日志。

prompt 由调用方决定，admin 不改内容，只负责 key、计时与记录。失败的调用也会被收费，所以照样记录；
Voyage 的 429（限流）不收费，不记录，交给调用方等待后重试。
"""

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings
from app.core.base_schema import AuthSchema

from ..call_log.schema import AiCallBatchSchema, AiCallReportSchema
from ..call_log.service import AiCallLogService
from .schema import AiGatewayRequestSchema

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
ANTHROPIC_TIMEOUT_SECONDS = 120
VOYAGE_TIMEOUT_SECONDS = 60


class AiGatewaySettings(BaseSettings):
    model_config = Settings.model_config
    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""


def keys() -> AiGatewaySettings:
    return AiGatewaySettings()


class AiNotConfigured(Exception):
    pass


async def _record(
    db: AsyncSession,
    request: AiGatewayRequestSchema,
    *,
    provider: str,
    usage: dict[str, int],
    duration_ms: int,
    error: str | None,
) -> None:
    caller = request.caller
    system = caller.platform == "system"
    log = AiCallReportSchema(
        project=request.project,
        feature=request.feature,
        provider=provider,
        model=str(request.body.get("model") or "unknown")[:128],
        trigger=caller.trigger,
        user_platform=caller.platform,
        user_external_id=None if system else caller.id,
        user_name=caller.name[:128] if caller.name else None,
        user_avatar_url=caller.avatar_url if caller.avatar_url and len(caller.avatar_url) <= 512 else None,
        input_tokens=usage.get("input") or 0,
        output_tokens=usage.get("output") or 0,
        cache_read_tokens=usage.get("cache_read") or 0,
        cache_write_tokens=usage.get("cache_write") or 0,
        duration_ms=duration_ms,
        success=error is None,
        error_message=error[:20000] if error else None,
        occurred_at=datetime.now(UTC),
    )
    await AiCallLogService(AuthSchema(), db).report(AiCallBatchSchema(logs=[log]))


async def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int | None, Any, str | None, int]:
    """(status, JSON body, transport error, duration ms)"""
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as error:
        return None, None, f"{type(error).__name__}: {error}", round((time.monotonic() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload, None, round((time.monotonic() - started) * 1000)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def claude(db: AsyncSession, request: AiGatewayRequestSchema) -> tuple[int | None, Any]:
    key = keys().ANTHROPIC_API_KEY
    if not key:
        raise AiNotConfigured("admin 没有设定 ANTHROPIC_API_KEY")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    status, payload, error, duration = await _post(ANTHROPIC_URL, headers, request.body, ANTHROPIC_TIMEOUT_SECONDS)
    if status is not None and not 200 <= status < 300:
        detail = _dict(_dict(payload).get("error")).get("message")
        error = f"Anthropic API returned HTTP {status}: {detail or 'no details'}"
    usage = _dict(_dict(payload).get("usage"))
    await _record(
        db,
        request,
        provider="anthropic",
        usage={
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
        },
        duration_ms=duration,
        error=error,
    )
    return status, payload


async def voyage(db: AsyncSession, request: AiGatewayRequestSchema) -> tuple[int | None, Any]:
    key = keys().VOYAGE_API_KEY
    if not key:
        raise AiNotConfigured("admin 没有设定 VOYAGE_API_KEY")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    status, payload, error, duration = await _post(VOYAGE_URL, headers, request.body, VOYAGE_TIMEOUT_SECONDS)
    if status == 429:
        return status, payload
    if status is not None and not 200 <= status < 300:
        error = f"Voyage returned HTTP {status}: {_dict(payload).get('detail') or 'no details'}"
    usage = _dict(_dict(payload).get("usage"))
    await _record(db, request, provider="voyage", usage={"input": usage.get("total_tokens")}, duration_ms=duration, error=error)
    return status, payload
