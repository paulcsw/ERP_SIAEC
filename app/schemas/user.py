"""User schemas (§8.8)."""
from datetime import datetime

from pydantic import BaseModel


class UserMeResponse(BaseModel):
    user_id: int
    employee_no: str
    display_name: str
    roles: list[str]
    team: str | None = None


class UserCreate(BaseModel):
    employee_no: str
    name: str
    email: str | None = None
    team: str | None = None
    roles: list[str] = []


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    team: str | None = None
    is_active: bool | None = None
    roles: list[str] | None = None


class UserResponse(BaseModel):
    id: int
    employee_no: str
    name: str
    email: str | None = None
    team: str | None = None
    is_active: bool
    roles: list[str]
    created_at: datetime
    updated_at: datetime
