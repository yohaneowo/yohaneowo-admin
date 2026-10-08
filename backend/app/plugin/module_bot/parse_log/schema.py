from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

from ..group.schema import Platform
from ..log_common import MAX_LOG_BATCH

# ── 后台 ──────────────────────────────────────────────────────────


class BotParseLogOutSchema(BaseSchema):
    """解析日志响应模型"""

    model_config = ConfigDict(from_attributes=True)

    platform: str
    source: str
    group_external_id: str | None = None
    group_name: str | None = Field(default=None, description="群组名称（按 bot_group 关联，未登记或私聊为空）")
    user_external_id: str | None = None
    user_name: str | None = None
    url: str
    site: str | None = None
    result: str
    error_message: str | None = None
    duration_ms: int | None = None
    file_count: int | None = None
    compressed: bool | None = None
    occurred_time: DateTimeStr


class BotParseLogQueryParam(BaseModel):
    """解析日志查询参数"""

    platform: str | None = Field(None, description="平台", json_schema_extra={"q": "eq"})
    source: str | None = Field(None, description="触发方式", json_schema_extra={"q": "eq"})
    site: str | None = Field(None, description="网站", json_schema_extra={"q": "eq"})
    result: str | None = Field(None, description="结果", json_schema_extra={"q": "eq"})
    group_external_id: str | None = Field(None, description="群组平台侧ID", json_schema_extra={"q": "eq"})
    user_name: str | None = Field(None, description="用户名称", json_schema_extra={"q": "like"})
    url: str | None = Field(None, description="链接", json_schema_extra={"q": "like"})
    occurred_time: list[DateTimeStr] | None = Field(None, description="发生时间范围")


# ── bot 内部 API ──────────────────────────────────────────────────


class BotParseLogReportSchema(BaseModel):
    """bot 上报的一条解析日志"""

    platform: Platform
    source: Literal["auto", "command"]
    group_external_id: str | None = Field(default=None, max_length=64)
    user_external_id: str | None = Field(default=None, max_length=64)
    user_name: str | None = Field(default=None, max_length=128)
    url: str = Field(..., min_length=1, max_length=2048)
    site: str | None = Field(default=None, max_length=32)
    result: Literal["success", "failed", "busy"]
    error_message: str | None = Field(default=None, max_length=20000)
    duration_ms: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    compressed: bool | None = None
    occurred_at: datetime


class BotParseLogBatchSchema(BaseModel):
    logs: list[BotParseLogReportSchema] = Field(..., max_length=MAX_LOG_BATCH)
