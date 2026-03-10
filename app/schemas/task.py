"""Task schemas (§8.4)."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


# ── §8.4.3 Create task + snapshot ─────────────────────────────────────

class TaskCreate(BaseModel):
    meeting_date: date
    shop_id: int
    aircraft_id: int
    work_package_id: int | None = None
    assigned_supervisor_id: int | None = None
    planned_mh: Decimal | None = None
    task_text: str
    status: str = "NOT_STARTED"
    mh_incurred_hours: Decimal = Decimal("0")
    deadline_date: date | None = None
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool = False


class TaskCreateResponse(BaseModel):
    task_id: int
    snapshot_id: int
    meeting_date: date
    shop_id: int
    aircraft_id: int
    work_package_id: int | None = None
    rfo_no: str | None = None
    assigned_supervisor_id: int | None = None
    assigned_worker_id: int | None = None
    distributed_at: datetime | None = None
    planned_mh: Decimal | None = None
    task_text: str
    status: str
    mh_incurred_hours: Decimal
    deadline_date: date | None = None
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool
    correction_reason: str | None = None
    version: int
    supervisor_updated_at: datetime | None = None
    last_updated_at: datetime
    last_updated_by: int


# ── §8.4.2 List snapshots ────────────────────────────────────────────

class SnapshotListItem(BaseModel):
    snapshot_id: int
    task_id: int
    meeting_date: date
    aircraft_id: int
    work_package_id: int | None = None
    rfo_no: str | None = None
    ac_reg: str
    shop_id: int
    shop_name: str
    assigned_supervisor_id: int | None = None
    assigned_supervisor_name: str | None = None
    assigned_worker_id: int | None = None
    assigned_worker_name: str | None = None
    distributed_at: datetime | None = None
    planned_mh: Decimal | None = None
    task_text: str
    status: str
    mh_incurred_hours: Decimal
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool
    deadline_date: date | None = None
    correction_reason: str | None = None
    is_deleted: bool
    version: int
    supervisor_updated_at: datetime | None = None
    last_updated_at: datetime
    last_updated_by: int
    is_active: bool


# ── §8.4.4 Update snapshot ───────────────────────────────────────────

class SnapshotUpdate(BaseModel):
    version: int
    status: str | None = None
    mh_incurred_hours: Decimal | None = None
    deadline_date: date | None = None
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool | None = None
    correction_reason: str | None = None


class SnapshotUpdateResponse(BaseModel):
    snapshot_id: int
    version: int
    status: str
    mh_incurred_hours: Decimal
    deadline_date: date | None = None
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool
    correction_reason: str | None = None
    last_updated_at: datetime
    last_updated_by: int
    supervisor_updated_at: datetime | None = None


# ── §8.4.5 Batch update ───────────────────────────────────────────────

class BatchUpdateItem(BaseModel):
    snapshot_id: int
    version: int
    status: str | None = None
    mh_incurred_hours: Decimal | None = None
    deadline_date: date | None = None
    remarks: str | None = None
    critical_issue: str | None = None
    has_issue: bool | None = None
    correction_reason: str | None = None


class BatchUpdateRequest(BaseModel):
    updates: list[BatchUpdateItem]


class BatchUpdateResponse(BaseModel):
    items: list[SnapshotUpdateResponse]


# ── §8.4.7 Soft delete / §8.4.8 Restore ─────────────────────────────

class SnapshotVersionRequest(BaseModel):
    version: int


class SnapshotDeleteResponse(BaseModel):
    snapshot_id: int
    is_deleted: bool
    version: int
    deleted_at: datetime | None = None
    deleted_by: int | None = None


class SnapshotRestoreResponse(BaseModel):
    snapshot_id: int
    is_deleted: bool
    version: int
    deleted_at: datetime | None = None
    deleted_by: int | None = None


# ── §8.4.6 Deactivate / Reactivate ──────────────────────────────────

class TaskDeactivateResponse(BaseModel):
    task_id: int
    is_active: bool
    deactivated_at: datetime | None = None
    deactivated_by: int | None = None


# ── §8.4.1 Init week ─────────────────────────────────────────────────

class InitWeekRequest(BaseModel):
    meeting_date: date
    shop_id: int


class InitWeekResponse(BaseModel):
    meeting_date: date
    shop_id: int
    created_count: int
    skipped_count: int
