from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

from ..group.schema import Platform
from ..log_common import MAX_LOG_BATCH, OTHER_SITE

# ── 后台 ──────────────────────────────────────────────────────────


class BotRuntimeLogOutSchema(BaseSchema):
    """运行日志响应模型"""

    model_config = ConfigDict(from_attributes=True)

    source: str
    level: str
    site: str | None = None
    message: str
    occurred_time: DateTimeStr


class BotRuntimeLogQueryParam(BaseModel):
    """运行日志查询参数"""

    source: str | None = Field(None, description="来源进程", json_schema_extra={"q": "eq"})
    level: str | None = Field(None, description="等级", json_schema_extra={"q": "eq"})
    site: str | None = Field(None, description=f"网站；{OTHER_SITE} 表示与解析无关的日志", json_schema_extra={"q": "eq"})
    message: str | None = Field(None, description="内容", json_schema_extra={"q": "like"})
    occurred_time: list[DateTimeStr] | None = Field(None, description="发生时间范围")


# ── bot 内部 API ──────────────────────────────────────────────────


class BotRuntimeLogReportSchema(BaseModel):
    """bot 上报的一条运行日志"""

    source: Platform
    level: Literal["info", "warn", "error"]
    site: str | None = Field(default=None, max_length=32)
    message: str = Field(..., max_length=20000)
    occurred_at: datetime


class BotRuntimeLogBatchSchema(BaseModel):
    logs: list[BotRuntimeLogReportSchema] = Field(..., max_length=MAX_LOG_BATCH)
