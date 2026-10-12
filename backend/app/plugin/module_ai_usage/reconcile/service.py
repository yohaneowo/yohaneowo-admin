"""费用对账：把 Anthropic 帐单上每天各模型的实际 token / 费用，跟 AI 调用日志的记录比对。

- 日期一律 UTC（与供应商帐单的分日一致）；只对到昨天，因为当天的资料还不完整。
- 每次重新对最近 RECHECK_DAYS 天，供应商的资料可能延迟几小时才补齐。
- 从 AI 调用日志的第一笔记录那天开始对，更早的用量本来就没有记录，比了只会全是「漏记」。
- 判断：token 与金额都一致 → ok；token 一致、金额不一致 → 单价不对；实际 token 比较多 → 有调用没记录；
  记录比较多 → 记录比实际多。帐单会四舍五入，所以金额在 5% 或 1 美分以内算一致，token 在 2% 以内算一致。
"""

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_db_session
from app.core.logger import logger

from ..anthropic_admin import admin_api_configured, fetch_day
from ..call_log.model import AiCallLogModel
from .model import AiCostReconcileModel
from .schema import AiCostReconcileOutSchema, AiCostReconcileRunSchema, AiCostReconcileSummarySchema

PROVIDER = "anthropic"
RECHECK_DAYS = 3
COST_TOLERANCE_RATIO = Decimal("0.05")
COST_TOLERANCE_USD = Decimal("0.01")
TOKEN_TOLERANCE_RATIO = Decimal("0.02")
BACKGROUND_INTERVAL_SECONDS = 6 * 60 * 60
LIST_LIMIT = 500

_run_lock = asyncio.Lock()
_last_background_run = 0.0
_background_tasks: set[asyncio.Task] = set()


def _close(actual: Decimal, logged: Decimal, ratio: Decimal, absolute: Decimal = Decimal(0)) -> bool:
    return abs(actual - logged) <= max(abs(actual) * ratio, absolute)


def classify(actual: dict[str, Any], logged: dict[str, Any]) -> str:
    actual_tokens = Decimal(actual["input"] + actual["output"] + actual["cache_read"] + actual["cache_write"])
    logged_tokens = Decimal(logged["input"] + logged["output"] + logged["cache_read"] + logged["cache_write"])
    tokens_ok = _close(actual_tokens, logged_tokens, TOKEN_TOLERANCE_RATIO)
    cost_ok = _close(actual["cost_usd"], logged["cost_usd"], COST_TOLERANCE_RATIO, COST_TOLERANCE_USD)
    if tokens_ok and cost_ok:
        return "ok"
    if tokens_ok:
        return "price_mismatch"
    return "missing_logs" if actual_tokens > logged_tokens else "extra_logs"


def _utc_today() -> date:
    return datetime.now(UTC).date()


async def first_logged_day(db: AsyncSession) -> date | None:
    first = (await db.execute(select(func.min(AiCallLogModel.occurred_time)).where(AiCallLogModel.provider == PROVIDER, AiCallLogModel.is_deleted.is_(False)))).scalar()
    return first.date() if first else None


async def _logged_for_day(db: AsyncSession, day: date) -> dict[str, dict[str, Any]]:
    """AI 调用日志里这一天（UTC）各模型的加总。occurred_time 存的是 UTC"""
    start = datetime.combine(day, datetime.min.time())
    log = AiCallLogModel
    rows = await db.execute(
        select(
            log.model,
            func.count(),
            func.coalesce(func.sum(log.input_tokens), 0),
            func.coalesce(func.sum(log.output_tokens), 0),
            func.coalesce(func.sum(log.cache_read_tokens), 0),
            func.coalesce(func.sum(log.cache_write_tokens), 0),
            func.coalesce(func.sum(log.cost_usd), 0),
            func.coalesce(func.sum(case((log.cost_usd.is_(None), 1), else_=0)), 0),
        )
        .where(log.provider == PROVIDER, log.is_deleted.is_(False), log.occurred_time >= start, log.occurred_time < start + timedelta(days=1))
        .group_by(log.model)
    )
    return {
        model: {"count": count, "input": int(i), "output": int(o), "cache_read": int(cr), "cache_write": int(cw), "cost_usd": Decimal(cost), "unpriced": int(unpriced)}
        for model, count, i, o, cr, cw, cost, unpriced in rows.all()
    }


async def reconcile(db: AsyncSession) -> AiCostReconcileRunSchema:
    """对最近几天（到昨天为止）重新对账，整天的结果重写"""
    first = await first_logged_day(db)
    yesterday = _utc_today() - timedelta(days=1)
    if first is None or first > yesterday:
        return AiCostReconcileRunSchema(days=[], rows=0, problems=0)

    start = max(first, yesterday - timedelta(days=RECHECK_DAYS - 1))
    days = [start + timedelta(days=offset) for offset in range((yesterday - start).days + 1)]
    empty = {"count": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": Decimal(0), "unpriced": 0}
    now = datetime.now(UTC)
    rows = problems = 0
    for day in days:
        actual_by_model = await fetch_day(day)
        logged_by_model = await _logged_for_day(db, day)
        await db.execute(delete(AiCostReconcileModel).where(AiCostReconcileModel.day == day, AiCostReconcileModel.provider == PROVIDER))
        for model in sorted(set(actual_by_model) | set(logged_by_model)):
            actual = actual_by_model.get(model) or {**empty}
            logged = logged_by_model.get(model) or {**empty}
            status = classify(actual, logged)
            db.add(
                AiCostReconcileModel(
                    day=day,
                    provider=PROVIDER,
                    model=model,
                    actual_input_tokens=actual["input"],
                    actual_output_tokens=actual["output"],
                    actual_cache_read_tokens=actual["cache_read"],
                    actual_cache_write_tokens=actual["cache_write"],
                    actual_cost_usd=actual["cost_usd"],
                    logged_call_count=logged["count"],
                    logged_input_tokens=logged["input"],
                    logged_output_tokens=logged["output"],
                    logged_cache_read_tokens=logged["cache_read"],
                    logged_cache_write_tokens=logged["cache_write"],
                    logged_cost_usd=logged["cost_usd"],
                    logged_unpriced_count=logged["unpriced"],
                    status=status,
                    checked_time=now,
                )
            )
            rows += 1
            problems += status != "ok"
    await db.flush()
    logger.info(f"AI 费用对账完成：{len(days)} 天、{rows} 笔，其中 {problems} 笔不一致")
    return AiCostReconcileRunSchema(days=days, rows=rows, problems=problems)


async def list_rows(db: AsyncSession, status: str | None = None) -> list[AiCostReconcileOutSchema]:
    query = select(AiCostReconcileModel).where(AiCostReconcileModel.is_deleted.is_(False))
    if status:
        query = query.where(AiCostReconcileModel.status == status)
    rows = await db.execute(query.order_by(AiCostReconcileModel.day.desc(), AiCostReconcileModel.model).limit(LIST_LIMIT))
    return [AiCostReconcileOutSchema.model_validate(row) for row in rows.scalars().all()]


async def summary(db: AsyncSession) -> AiCostReconcileSummarySchema:
    today = _utc_today()
    month_start = today.replace(day=1)
    model = AiCostReconcileModel
    actual, logged, problem_count, last_checked = (
        await db.execute(
            select(
                func.coalesce(func.sum(model.actual_cost_usd), 0),
                func.coalesce(func.sum(model.logged_cost_usd), 0),
                func.coalesce(func.sum(case((model.status != "ok", 1), else_=0)), 0),
                func.max(model.checked_time),
            ).where(model.is_deleted.is_(False), model.day >= month_start)
        )
    ).one()
    return AiCostReconcileSummarySchema(
        configured=admin_api_configured(),
        month=month_start.strftime("%Y-%m"),
        actual_cost_usd=Decimal(actual),
        logged_cost_usd=Decimal(logged),
        problem_count=int(problem_count),
        first_day=await first_logged_day(db),
        last_checked_time=last_checked,
    )


async def _reconcile_if_due() -> None:
    global _last_background_run
    if not admin_api_configured() or _run_lock.locked() or time.monotonic() - _last_background_run < BACKGROUND_INTERVAL_SECONDS:
        return
    async with _run_lock:
        _last_background_run = time.monotonic()
        try:
            async with async_db_session() as db, db.begin():
                await reconcile(db)
        except Exception as error:
            logger.warning(f"AI 费用对账失败，稍后再试：{error}")


def reconcile_in_background() -> None:
    """超过 BACKGROUND_INTERVAL 没对账就在背景对一次，不阻塞当前请求"""
    task = asyncio.create_task(_reconcile_if_due())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def run_now(db: AsyncSession) -> AiCostReconcileRunSchema:
    """手动对账（「立即对账」按钮）"""
    async with _run_lock:
        return await reconcile(db)
