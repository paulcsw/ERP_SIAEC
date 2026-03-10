"""Reference data schemas (§8.6)."""
from datetime import date, datetime

from pydantic import BaseModel


# ── Aircraft ────────────────────────────────────────────────────────

class AircraftCreate(BaseModel):
    ac_reg: str
    airline: str | None = None
    status: str = "ACTIVE"


class AircraftUpdate(BaseModel):
    airline: str | None = None
    status: str | None = None


class AircraftResponse(BaseModel):
    id: int
    ac_reg: str
    airline: str | None = None
    status: str
    created_at: datetime


# ── Work Package ────────────────────────────────────────────────────

class WorkPackageCreate(BaseModel):
    aircraft_id: int
    rfo_no: str | None = None
    title: str
    start_date: date | None = None
    end_date: date | None = None
    priority: int | None = 0


class WorkPackageUpdate(BaseModel):
    rfo_no: str | None = None
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    priority: int | None = None
    status: str | None = None


class WorkPackageResponse(BaseModel):
    id: int
    aircraft_id: int
    rfo_no: str | None = None
    title: str
    start_date: date | None = None
    end_date: date | None = None
    priority: int | None = None
    status: str
    created_at: datetime


# ── Shop Stream ─────────────────────────────────────────────────────

class ShopStreamCreate(BaseModel):
    work_package_id: int
    shop_code: str
    status: str = "ACTIVE"


class ShopStreamUpdate(BaseModel):
    shop_code: str | None = None
    status: str | None = None


class ShopStreamResponse(BaseModel):
    id: int
    work_package_id: int
    shop_code: str
    status: str
    created_at: datetime
