from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr


class AiCostReconcileOutSchema(BaseSchema):
    """一天一个模型的对账结果"""

    model_config = ConfigDict(from_attributes=True)

    day: date
    provider: str
    model: str
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cache_read_tokens: int
    actual_cache_write_tokens: int
    actual_cost_usd: Decimal
    logged_call_count: int
    logged_input_tokens: int
    logged_output_tokens: int
    logged_cache_read_tokens: int
    logged_cache_write_tokens: int
    logged_cost_usd: Decimal
    logged_unpriced_count: int
    status: str
    checked_time: DateTimeStr


class AiCostReconcileSummarySchema(BaseModel):
    """本月（UTC）合计与对账状态"""

    configured: bool = Field(..., description="是否设定了 ANTHROPIC_ADMIN_API_KEY")
    month: str = Field(..., description="UTC 月份，如 2026-10")
    actual_cost_usd: Decimal
    logged_cost_usd: Decimal
    problem_count: int = Field(..., description="本月结果不是一致的笔数")
    first_day: date | None = Field(default=None, description="从哪一天开始对账（AI 调用日志的第一笔，UTC）")
    last_checked_time: DateTimeStr | None = None


class AiCostReconcileRunSchema(BaseModel):
    """一次对账的结果"""

    days: list[date]
    rows: int
    problems: int
