"""System config schemas (§8.9)."""
from datetime import datetime

from pydantic import BaseModel


class ConfigItem(BaseModel):
    key: str
    value: str
    updated_at: datetime | None = None


class ConfigListResponse(BaseModel):
    configs: list[ConfigItem]


class ConfigUpdateItem(BaseModel):
    key: str
    value: str


class ConfigBatchUpdate(BaseModel):
    configs: list[ConfigUpdateItem]


class ConfigUpdateResponse(BaseModel):
    updated: int


class ConfigSingleResponse(BaseModel):
    key: str
    value: str
