from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseSchema
from app.core.validator import DateTimeStr


class AiModelPriceOutSchema(BaseSchema):
    """模型单价响应模型"""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    model: str
    input_price: Decimal
    output_price: Decimal
    cache_read_price: Decimal | None = None
    cache_write_price: Decimal | None = None
    source: str
    locked: bool
    synced_time: DateTimeStr | None = None


class AiModelPriceQueryParam(BaseModel):
    """模型单价查询参数"""

    provider: str | None = Field(None, description="供应商", json_schema_extra={"q": "eq"})
    model: str | None = Field(None, description="模型", json_schema_extra={"q": "like"})
    source: str | None = Field(None, description="来源", json_schema_extra={"q": "eq"})


class AiModelPriceSaveSchema(BaseModel):
    """手动新增 / 修改单价。手动保存的价格默认锁定，不被自动同步覆盖"""

    provider: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    input_price: Decimal = Field(..., ge=0)
    output_price: Decimal = Field(..., ge=0)
    cache_read_price: Decimal | None = Field(default=None, ge=0)
    cache_write_price: Decimal | None = Field(default=None, ge=0)
    locked: bool = True
    description: str | None = Field(default=None, max_length=255)


class AiPriceSyncResultSchema(BaseModel):
    """一次同步的结果"""

    added: int = Field(..., description="新增的模型数")
    updated: int = Field(..., description="价格有变动的模型数")
    skipped_locked: int = Field(..., description="因锁定而跳过的模型数")
    repriced_calls: int = Field(..., description="补算了费用的调用记录数")


class AiPriceStatusSchema(BaseModel):
    """价格表状态"""

    last_synced_time: DateTimeStr | None = Field(default=None, description="最近一次同步时间")
    model_count: int = Field(..., description="模型数")
    providers: list[str] = Field(default_factory=list, description="价格表里有的供应商（搜索栏选项）")
    unpriced_call_count: int = Field(..., description="还没有价格（未定价）的调用记录数")
