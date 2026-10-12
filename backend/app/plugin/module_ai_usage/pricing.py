"""模型单价：从 LiteLLM 公开价格表同步，以及按单价计算一次调用的费用。

Anthropic、Voyage 等供应商都没有查询价格的 API；LiteLLM 维护的 model_prices_and_context_window.json
是业界常用的价格来源，新模型上市后通常很快会收录。它是社群维护的，偶尔有延迟或错误，
所以手动改过的单价会锁定，不被同步覆盖。

同步不靠排程：价格表超过 SYNC_INTERVAL 没更新时，收到调用上报或打开价格页面就在背景同步一次。
各项目每天都会上报，实际上就是每天同步一次。
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_db_session
from app.core.logger import logger

from .call_log.model import AiCallLogModel
from .price.model import AiModelPriceModel

LITELLM_PRICES_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# 只同步会用到的供应商，避免价格表塞满几千个用不到的模型
PROVIDERS = {"anthropic", "voyage", "openai", "deepseek", "gemini"}
MODES = {"chat", "completion", "embedding"}
SYNC_INTERVAL = timedelta(hours=24)
CHECK_INTERVAL_SECONDS = 60 * 60
PER_MILLION = Decimal(1_000_000)
PRICE_QUANTUM = Decimal("0.000001")
COST_QUANTUM = Decimal("0.00000001")

_sync_lock = asyncio.Lock()
_last_attempt = 0.0
# 背景任务要保留引用，否则可能在执行中被回收
_background_tasks: set[asyncio.Task] = set()


def _per_million(per_token: Any) -> Decimal | None:
    if per_token is None:
        return None
    return (Decimal(str(per_token)) * PER_MILLION).quantize(PRICE_QUANTUM)


async def fetch_litellm_prices() -> dict[tuple[str, str], dict[str, Decimal | None]]:
    """(provider, model) -> 各项单价（美元/百万token）。model 去掉 LiteLLM 加的 "provider/" 前缀，与 API 请求里的名称一致"""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(LITELLM_PRICES_URL)
        response.raise_for_status()
        data = response.json()

    prices: dict[tuple[str, str], dict[str, Decimal | None]] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        provider = entry.get("litellm_provider")
        if provider not in PROVIDERS or entry.get("mode") not in MODES or entry.get("input_cost_per_token") is None:
            continue
        prefixed = key.startswith(f"{provider}/")
        model = key.split("/", 1)[1] if prefixed else key
        # 剩下还带 "/" 的是区域或微调变体（如 bedrock 路径），用不到
        if "/" in model:
            continue
        # 同一个模型同时有 "claude-x" 与 "anthropic/claude-x" 时，以不带前缀的为准
        if (provider, model) in prices and prefixed:
            continue
        prices[(provider, model)] = {
            "input_price": _per_million(entry.get("input_cost_per_token")) or Decimal(0),
            "output_price": _per_million(entry.get("output_cost_per_token")) or Decimal(0),
            "cache_read_price": _per_million(entry.get("cache_read_input_token_cost")),
            "cache_write_price": _per_million(entry.get("cache_creation_input_token_cost")),
        }
    return prices


def call_cost(price: AiModelPriceModel, call: AiCallLogModel) -> Decimal:
    """一次调用的费用（美元）。没有快取单价时，快取 token 按输入单价算"""
    cache_read = price.cache_read_price if price.cache_read_price is not None else price.input_price
    cache_write = price.cache_write_price if price.cache_write_price is not None else price.input_price
    total = call.input_tokens * price.input_price + call.output_tokens * price.output_price + call.cache_read_tokens * cache_read + call.cache_write_tokens * cache_write
    return (total / PER_MILLION).quantize(COST_QUANTUM)


async def load_price_map(db: AsyncSession) -> dict[tuple[str, str], AiModelPriceModel]:
    rows = await db.execute(select(AiModelPriceModel).where(AiModelPriceModel.is_deleted.is_(False)))
    return {(row.provider, row.model): row for row in rows.scalars().all()}


async def reprice_unpriced(db: AsyncSession) -> int:
    """给还没有费用（未定价）的调用补算费用，返回补上的笔数"""
    price_map = await load_price_map(db)
    rows = await db.execute(select(AiCallLogModel).where(AiCallLogModel.cost_usd.is_(None), AiCallLogModel.is_deleted.is_(False)))
    repriced = 0
    for call in rows.scalars().all():
        price = price_map.get((call.provider, call.model))
        if price is not None:
            call.cost_usd = call_cost(price, call)
            repriced += 1
    await db.flush()
    return repriced


async def sync_prices(db: AsyncSession) -> dict[str, int]:
    """从 LiteLLM 同步单价：新增没有的，更新没锁定的，再补算未定价的调用"""
    fetched = await fetch_litellm_prices()
    existing = await load_price_map(db)
    now = datetime.now(UTC)
    added = updated = skipped_locked = 0
    for (provider, model), values in fetched.items():
        row = existing.get((provider, model))
        if row is None:
            db.add(AiModelPriceModel(provider=provider, model=model, source="litellm", locked=False, synced_time=now, **values))
            added += 1
            continue
        if row.locked:
            skipped_locked += 1
            continue
        if any(getattr(row, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(row, field, value)
            updated += 1
        row.source = "litellm"
        row.synced_time = now
    await db.flush()
    repriced = await reprice_unpriced(db)
    logger.info(f"AI 单价同步完成：新增 {added}、更新 {updated}、锁定跳过 {skipped_locked}、补算 {repriced} 笔调用")
    return {"added": added, "updated": updated, "skipped_locked": skipped_locked, "repriced_calls": repriced}


async def last_synced_time(db: AsyncSession) -> datetime | None:
    return (await db.execute(select(func.max(AiModelPriceModel.synced_time)))).scalar()


async def _sync_if_stale() -> None:
    global _last_attempt
    if _sync_lock.locked() or time.monotonic() - _last_attempt < CHECK_INTERVAL_SECONDS:
        return
    async with _sync_lock:
        _last_attempt = time.monotonic()
        try:
            async with async_db_session() as db, db.begin():
                last = await last_synced_time(db)
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                # 价格还新的话，下一次检查也在一小时后，免得每次上报都查一次
                if last is None or datetime.now(UTC) - last >= SYNC_INTERVAL:
                    await sync_prices(db)
        except Exception as error:
            logger.warning(f"AI 单价同步失败，一小时后再试：{error}")


def refresh_prices_in_background() -> None:
    """价格表过期就在背景同步，不阻塞当前请求"""
    task = asyncio.create_task(_sync_if_stale())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
