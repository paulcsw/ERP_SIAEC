"""OT request/response schemas (§8.3)."""
from datetime import date, datetime, time

from pydantic import BaseModel


# ── Submit ──────────────────────────────────────────────────────────

class OtSubmit(BaseModel):
    date: date
    start_time: time
    end_time: time
    requested_minutes: int | None = None
    reason_code: str = "OTHER"
    reason_text: str | None = None
    work_package_id: int | None = None
    shop_stream_id: int | None = None
    user_ids: list[int] | None = None  # bulk/proxy


# ── Single OT response ─────────────────────────────────────────────

class OtResponse(BaseModel):
    id: int
    user_id: int
    user_name: str | None = None
    submitted_by: int | None = None
    submitted_by_name: str | None = None
    date: date
    start_time: time
    end_time: time
    requested_minutes: int
    reason_code: str
    reason_text: str | None = None
    work_package_id: int | None = None
    shop_stream_id: int | None = None
    status: str
    created_at: datetime


# ── Bulk response ──────────────────────────────────────────────────

class BulkOtCreatedItem(BaseModel):
    id: int
    user_id: int
    submitted_by: int | None = None
    status: str


class BulkOtSkippedItem(BaseModel):
    user_id: int
    reason: str


class BulkOtResponse(BaseModel):
    created: list[BulkOtCreatedItem]
    skipped: list[BulkOtSkippedItem]
    created_count: int
    skipped_count: int


# ── Endorse / Approve ──────────────────────────────────────────────

class OtApprovalRequest(BaseModel):
    action: str  # APPROVE or REJECT
    comment: str | None = None


class OtApprovalResponse(BaseModel):
    ot_request_id: int
    stage: str
    action: str
    approver_id: int
    approver_name: str | None = None
    comment: str | None = None
    acted_at: datetime
    ot_request: OtResponse
