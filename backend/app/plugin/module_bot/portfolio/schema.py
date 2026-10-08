from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr

# ── 后台 ──────────────────────────────────────────────────────────


class PortfolioAccountSchema(BaseModel):
    exchange: str = Field(..., max_length=32)
    type: str = Field(..., max_length=32, description="spot / futures / earn / funding / account / inverse-contract-wallet")
    total: float = Field(..., description="USDT")


class BotPortfolioSnapshotOutSchema(BaseSchema):
    """资产快照响应模型"""

    model_config = ConfigDict(from_attributes=True)

    total_twd: float
    total_usdt: float
    twd_rate: float
    accounts: list[PortfolioAccountSchema]
    failures: list[str]
    occurred_time: DateTimeStr


class BotPortfolioLatestSchema(BaseModel):
    """首页总资产卡片：最新快照 + 与约 24 小时前的快照比较"""

    latest: BotPortfolioSnapshotOutSchema | None = None
    previous_total_twd: float | None = Field(default=None, description="24 小时前（或更早最近一笔）的总资产(TWD)")
    change_percent: float | None = Field(default=None, description="TWD 总资产相对 24 小时前的变化百分比")


class BotPortfolioQueryParam(BaseModel):
    """资产快照查询参数"""

    occurred_time: list[DateTimeStr] | None = Field(None, description="快照时间范围")


# ── bot 内部 API ──────────────────────────────────────────────────


class BotPortfolioReportSchema(BaseModel):
    """bot 上报的一次资产快照"""

    total_twd: float = Field(..., ge=0)
    total_usdt: float = Field(..., ge=0)
    twd_rate: float = Field(..., gt=0)
    accounts: list[PortfolioAccountSchema] = Field(default_factory=list, max_length=100)
    failures: list[str] = Field(default_factory=list, max_length=100)
    occurred_at: datetime
