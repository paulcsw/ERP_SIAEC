"""Shop & ShopAccess schemas (§8.5)."""
from datetime import datetime

from pydantic import BaseModel


# ── Shop ──────────────────────────────────────────────────────────────

class ShopCreate(BaseModel):
    code: str
    name: str


class ShopUpdate(BaseModel):
    code: str | None = None
    name: str | None = None


class ShopResponse(BaseModel):
    id: int
    code: str
    name: str
    created_at: datetime
    updated_at: datetime | None = None
    created_by: int | None = None


# ── ShopAccess ────────────────────────────────────────────────────────

class ShopAccessCreate(BaseModel):
    user_id: int
    shop_id: int
    access: str  # VIEW / EDIT / MANAGE


class ShopAccessUpdate(BaseModel):
    access: str  # VIEW / EDIT / MANAGE


class ShopAccessResponse(BaseModel):
    id: int
    user_id: int
    shop_id: int
    access: str
    granted_at: datetime
    granted_by: int
