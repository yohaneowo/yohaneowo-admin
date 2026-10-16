from typing import Any

from pydantic import BaseModel, Field


class AiCallerSchema(BaseModel):
    """费用归属：替谁做的事。共用的调用（如新片向量）是 system"""

    platform: str = Field(default="system", min_length=1, max_length=16)
    id: str | None = Field(default=None, max_length=64)
    name: str | None = None
    avatar_url: str | None = None
    trigger: str | None = Field(default=None, max_length=16)


class AiGatewayRequestSchema(BaseModel):
    """转发一次 AI 调用。body 原样送给供应商（Claude 是 Messages API 的请求体，Voyage 是 embeddings 的请求体）"""

    project: str = Field(..., min_length=1, max_length=64)
    feature: str = Field(..., min_length=1, max_length=64)
    caller: AiCallerSchema = Field(default_factory=AiCallerSchema)
    body: dict[str, Any]
