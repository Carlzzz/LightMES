from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["ERP Sync"])
    scopes: list[str] = Field(default=["read"], examples=[["read", "write"]])
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    """API Key 列表项 — 不含 key_hash，不含完整 key。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    """POST 创建后的响应 — full_key 仅此一次返回。"""
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    full_key: str  # 仅此一次
    created_at: datetime
