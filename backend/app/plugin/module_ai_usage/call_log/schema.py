from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

# 一次批量上报的上限，与 bot 端 services/adminApi.js 的 LOG_BATCH_SIZE 对应
MAX_CALL_BATCH = 500

# ── 后台 ──────────────────────────────────────────────────────────


class AiCallLogOutSchema(BaseSchema):
    """AI 调用记录响应模型"""

    model_config = ConfigDict(from_attributes=True)

    project: str
    feature: str
    provider: str
    model: str
    trigger: str | None = None
    user_platform: str
    user_external_id: str | None = None
    user_name: str | None = None
    user_avatar_url: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal | None = None
    duration_ms: int | None = None
    success: bool
    error_message: str | None = None
    occurred_time: DateTimeStr


class AiCallLogQueryParam(BaseModel):
    """调用记录查询参数（列表、统计、排行共用）"""

    project: str | None = Field(None, description="项目", json_schema_extra={"q": "eq"})
    feature: str | None = Field(None, description="功能", json_schema_extra={"q": "eq"})
    provider: str | None = Field(None, description="供应商", json_schema_extra={"q": "eq"})
    model: str | None = Field(None, description="模型", json_schema_extra={"q": "eq"})
    user_platform: str | None = Field(None, description="平台", json_schema_extra={"q": "eq"})
    user_external_id: str | None = Field(None, description="用户ID", json_schema_extra={"q": "eq"})
    user_name: str | None = Field(None, description="用户名称", json_schema_extra={"q": "like"})
    success: bool | None = Field(None, description="是否成功", json_schema_extra={"q": "eq"})
    occurred_time: list[DateTimeStr] | None = Field(None, description="调用时间范围")


class AiCallFiltersSchema(BaseModel):
    """搜索栏下拉选项：调用记录里出现过的值（免得手打拼错）"""

    projects: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class AiCallSummarySchema(BaseModel):
    """筛选范围内的合计"""

    call_count: int
    total_cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    unpriced_count: int = Field(..., description="没有单价、费用未计入的调用数")


RankingGroup = Literal["user", "project", "feature", "model"]


class AiCostRankingQueryParam(AiCallLogQueryParam):
    """费用排行的查询参数。group_by / limit 必须跟筛选条件放在同一个模型里：
    FastAPI 的查询参数模型旁边若还有其他查询参数，模型就不会被展开（会变成「缺少 search」）"""

    group_by: RankingGroup = Field("user", description="分组：user / project / feature / model")
    limit: int = Field(20, ge=1, le=100, description="前几名")

    def filters(self) -> AiCallLogQueryParam:
        return AiCallLogQueryParam(**self.model_dump(exclude={"group_by", "limit"}))


class AiCostRankingRowSchema(BaseModel):
    """费用排行的一行。group_by=user 时有用户栏位，其余只有 key"""

    key: str = Field(..., description="分组值：项目 / 功能 / 模型名称；用户时为 平台:ID")
    user_platform: str | None = None
    user_external_id: str | None = None
    user_name: str | None = None
    user_avatar_url: str | None = None
    call_count: int
    total_cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    last_time: DateTimeStr | None = None


# ── 内部 API（各项目上报） ────────────────────────────────────────


class AiCallReportSchema(BaseModel):
    """上报的一次 AI 调用。费用由 admin 按单价表计算，上报方只给 token 数"""

    project: str = Field(..., min_length=1, max_length=64)
    feature: str = Field(..., min_length=1, max_length=64)
    provider: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    trigger: str | None = Field(default=None, max_length=16)
    user_platform: str = Field(default="system", min_length=1, max_length=16)
    user_external_id: str | None = Field(default=None, max_length=64)
    user_name: str | None = Field(default=None, max_length=128)
    user_avatar_url: str | None = Field(default=None, max_length=512)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    success: bool = True
    error_message: str | None = Field(default=None, max_length=20000)
    occurred_at: datetime


class AiCallBatchSchema(BaseModel):
    logs: list[AiCallReportSchema] = Field(..., max_length=MAX_CALL_BATCH)
