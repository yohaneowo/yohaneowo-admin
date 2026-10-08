from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr

Platform = Literal["discord", "line"]


# ── 后台 ──────────────────────────────────────────────────────────


class BotGroupUpdateSchema(BaseModel):
    """后台只能改状态和备注；名称、成员数等由 bot 上报"""

    status: int | None = Field(default=None, ge=0, le=1, description="状态(0:启用 1:停用)")
    description: str | None = Field(default=None, max_length=255, description="备注")


class BotGroupOutSchema(BaseSchema):
    """Bot 群组响应模型"""

    model_config = ConfigDict(from_attributes=True)

    platform: str
    external_id: str
    name: str
    icon_url: str | None = None
    member_count: int | None = None
    status: int
    is_joined: bool
    joined_time: DateTimeStr | None = None
    left_time: DateTimeStr | None = None
    last_active_time: DateTimeStr | None = None
    description: str | None = None


class BotGroupQueryParam(BaseQueryParam):
    """Bot 群组查询参数"""

    name: str | None = Field(None, description="群组名称", json_schema_extra={"q": "like"})
    platform: str | None = Field(None, description="平台", json_schema_extra={"q": "eq"})
    external_id: str | None = Field(None, description="平台侧ID", json_schema_extra={"q": "eq"})
    status: int | None = Field(None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})
    is_joined: bool | None = Field(None, description="是否在群内", json_schema_extra={"q": "eq"})


# ── bot 内部 API ──────────────────────────────────────────────────


class BotGroupReportSchema(BaseModel):
    """bot 上报的单个群组信息"""

    external_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    icon_url: str | None = Field(default=None, max_length=512)
    member_count: int | None = Field(default=None, ge=0)


class BotGroupJoinSchema(BotGroupReportSchema):
    platform: Platform


class BotGroupSyncSchema(BaseModel):
    """bot 启动时上报所在的全部群组；同平台未出现在列表里的群组标记为已离开"""

    platform: Platform
    groups: list[BotGroupReportSchema] = Field(default_factory=list, max_length=1000)


class BotGroupRefSchema(BaseModel):
    platform: Platform
    external_id: str = Field(..., min_length=1, max_length=64)


class BotGroupStateSchema(BaseModel):
    """回给 bot 的群组状态，供之后按群开关功能使用"""

    model_config = ConfigDict(from_attributes=True)

    platform: str
    external_id: str
    status: int
