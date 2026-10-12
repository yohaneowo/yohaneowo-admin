"""Anthropic Usage & Cost Admin API：每天各模型的实际 token 与实际费用，用来跟 AI 调用日志对账。

需要 Admin API key（sk-ant-admin01-…，Console → Settings → Admin keys，只有组织管理员能建立），
放在 ANTHROPIC_ADMIN_API_KEY。资料按 UTC 日期分组；当天的资料可能延迟几小时才完整。
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

import httpx
from pydantic_settings import BaseSettings

from app.config.setting import Settings

API_URL = "https://api.anthropic.com/v1/organizations"
REQUEST_TIMEOUT_SECONDS = 30
MAX_PAGES = 20
CENTS_PER_USD = Decimal(100)


class AiUsageSettings(BaseSettings):
    """插件自己的配置，与框架共用同一份 env 文件"""

    model_config = Settings.model_config

    ANTHROPIC_ADMIN_API_KEY: str = ""


@lru_cache
def get_ai_usage_settings() -> AiUsageSettings:
    return AiUsageSettings()


def admin_api_configured() -> bool:
    return bool(get_ai_usage_settings().ANTHROPIC_ADMIN_API_KEY)


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min)
    return f"{start.isoformat()}Z", f"{(start + timedelta(days=1)).isoformat()}Z"


async def _get_all(client: httpx.AsyncClient, path: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """跟着 next_page 把所有分页的 bucket 拿完"""
    buckets: list[dict[str, Any]] = []
    page: str | None = None
    for _ in range(MAX_PAGES):
        response = await client.get(f"{API_URL}/{path}", params=params + ([("page", page)] if page else []))
        if response.status_code != 200:
            message = (response.json().get("error") or {}).get("message", response.text[:200]) if response.content else ""
            raise RuntimeError(f"Anthropic Admin API {path} 返回 HTTP {response.status_code}：{message}")
        body = response.json()
        buckets.extend(body.get("data") or [])
        page = body.get("next_page")
        if not body.get("has_more") or not page:
            break
    return buckets


async def fetch_day(day: date) -> dict[str, dict[str, Any]]:
    """某个 UTC 日期各模型的实际用量与费用：model -> {input, output, cache_read, cache_write, cost_usd}"""
    starting_at, ending_at = _day_bounds(day)
    headers = {"x-api-key": get_ai_usage_settings().ANTHROPIC_ADMIN_API_KEY, "anthropic-version": "2023-06-01"}
    result: dict[str, dict[str, Any]] = {}

    def row(model: str) -> dict[str, Any]:
        return result.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": Decimal(0)})

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, headers=headers) as client:
        usage = await _get_all(
            client,
            "usage_report/messages",
            [("starting_at", starting_at), ("ending_at", ending_at), ("bucket_width", "1d"), ("group_by[]", "model")],
        )
        for bucket in usage:
            for item in bucket.get("results") or []:
                if not item.get("model"):
                    continue
                entry = row(item["model"])
                cache_creation = item.get("cache_creation") or {}
                entry["input"] += item.get("uncached_input_tokens") or 0
                entry["output"] += item.get("output_tokens") or 0
                entry["cache_read"] += item.get("cache_read_input_tokens") or 0
                entry["cache_write"] += sum(value or 0 for value in cache_creation.values())

        costs = await _get_all(
            client,
            "cost_report",
            [("starting_at", starting_at), ("ending_at", ending_at), ("group_by[]", "description")],
        )
        for bucket in costs:
            for item in bucket.get("results") or []:
                # 金额单位是美分（小数字串）；没有模型的费用（例如工具调用）记在 "(other)"
                row(item.get("model") or "(other)")["cost_usd"] += Decimal(str(item.get("amount") or "0")) / CENTS_PER_USD
    return result
