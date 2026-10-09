from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

from ..log_common import MAX_LOG_BATCH

# ── 后台 ──────────────────────────────────────────────────────────


class BotVoiceSessionOutSchema(BaseSchema):
    """语音停留记录响应模型"""

    model_config = ConfigDict(from_attributes=True)

    guild_external_id: str
    group_name: str | None = Field(default=None, description="服务器名称（按 bot_group 关联）")
    group_icon_url: str | None = Field(default=None, description="服务器头像URL（按 bot_group 关联）")
    channel_external_id: str
    channel_name: str
    user_external_id: str
    user_name: str | None = None
    user_avatar_url: str | None = None
    joined_time: DateTimeStr
    joined_time_estimated: bool
    left_time: DateTimeStr
    duration_seconds: int
    end_reason: str


class BotVoiceSessionQueryParam(BaseModel):
    """语音停留记录查询参数"""

    guild_external_id: str | None = Field(None, description="服务器ID", json_schema_extra={"q": "eq"})
    channel_name: str | None = Field(None, description="频道名称", json_schema_extra={"q": "like"})
    user_name: str | None = Field(None, description="用户名称", json_schema_extra={"q": "like"})
    user_external_id: str | None = Field(None, description="用户ID", json_schema_extra={"q": "eq"})
    end_reason: str | None = Field(None, description="结束原因", json_schema_extra={"q": "eq"})
    joined_time: list[DateTimeStr] | None = Field(None, description="进入时间范围")


class BotVoiceUserStatSchema(BaseModel):
    """语音时长排行的一行"""

    user_external_id: str = Field(..., description="用户ID")
    user_name: str | None = Field(default=None, description="用户名称（最近一次记录的）")
    user_avatar_url: str | None = Field(default=None, description="用户头像URL（最近一次记录的）")
    total_seconds: int = Field(..., description="总停留秒数")
    session_count: int = Field(..., description="停留次数")


# ── bot 内部 API ──────────────────────────────────────────────────


class BotVoiceSessionReportSchema(BaseModel):
    """bot 上报的一段语音停留"""

    guild_external_id: str = Field(..., min_length=1, max_length=64)
    channel_external_id: str = Field(..., min_length=1, max_length=64)
    channel_name: str = Field(default="", max_length=100)
    user_external_id: str = Field(..., min_length=1, max_length=64)
    user_name: str | None = Field(default=None, max_length=128)
    user_avatar_url: str | None = Field(default=None, max_length=512)
    joined_at: datetime
    joined_time_estimated: bool = False
    left_at: datetime
    duration_seconds: int = Field(..., ge=0)
    end_reason: Literal["leave", "move", "restart"]


class BotVoiceSessionBatchSchema(BaseModel):
    logs: list[BotVoiceSessionReportSchema] = Field(..., max_length=MAX_LOG_BATCH)
