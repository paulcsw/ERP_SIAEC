"""OT API endpoints (§8.3)."""
import csv
import io
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.deps import get_current_user, get_db, require_role
from app.models.ot import OtApproval, OtRequest
from app.models.user import User
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.ot import (
    BulkOtResponse,
    OtApprovalRequest,
    OtApprovalResponse,
    OtResponse,
    OtSubmit,
)
from app.services.audit_service import write_audit
from app.services.ot_service import submit_bulk, submit_single

router = APIRouter(prefix="/api/ot", tags=["ot"])


# ── Helpers ─────────────────────────────────────────────────────────

async def _enrich_ot(db: AsyncSession, ot: OtRequest) -> dict:
    """Convert OtRequest to response dict with user names."""
    user = (await db.execute(select(User).where(User.id == ot.user_id))).scalar_one_or_none()
    user_name = user.name if user else None

    submitted_by_name = None
    if ot.submitted_by:
        sub = (await db.execute(select(User).where(User.id == ot.submitted_by))).scalar_one_or_none()
        submitted_by_name = sub.name if sub else None

    return {
        "id": ot.id,
        "user_id": ot.user_id,
        "user_name": user_name,
        "submitted_by": ot.submitted_by,
        "submitted_by_name": submitted_by_name,
        "date": ot.date,
        "start_time": ot.start_time,
        "end_time": ot.end_time,
        "requested_minutes": ot.requested_minutes,
        "reason_code": ot.reason_code,
        "reason_text": ot.reason_text,
        "work_package_id": ot.work_package_id,
        "shop_stream_id": ot.shop_stream_id,
        "status": ot.status,
        "created_at": ot.created_at,
    }


# ── POST /api/ot — submit (self / proxy / bulk) §8.3.1 ─────────────

@router.post("")
async def submit_ot(
    body: OtSubmit,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    actor_id = current_user["user_id"]
    actor_roles = current_user.get("roles", [])
    actor_team = current_user.get("team")
    is_admin = "ADMIN" in actor_roles
    is_supervisor = "SUPERVISOR" in actor_roles

    if body.user_ids:
        # ── Bulk / proxy ──
        if not (is_supervisor or is_admin):
            raise APIError(403, "Only SUPERVISOR+ can submit for others", "FORBIDDEN")

        result = await submit_bulk(
            db,
            actor_id=actor_id,
            actor_team=actor_team,
            actor_roles=actor_roles,
            user_ids=body.user_ids,
            ot_date=body.date,
            start_time=body.start_time,
            end_time=body.end_time,
            requested_minutes=body.requested_minutes,
            reason_code=body.reason_code,
            reason_text=body.reason_text,
            work_package_id=body.work_package_id,
            shop_stream_id=body.shop_stream_id,
        )
        await db.commit()
        return result

    else:
        # ── Self submit ──
        ot = await submit_single(
            db,
            actor_id=actor_id,
            target_user_id=actor_id,
            submitted_by=None,
            ot_date=body.date,
            start_time=body.start_time,
            end_time=body.end_time,
            requested_minutes=body.requested_minutes,
            reason_code=body.reason_code,
            reason_text=body.reason_text,
            work_package_id=body.work_package_id,
            shop_stream_id=body.shop_stream_id,
        )
        await db.commit()
        await db.refresh(ot)

        resp = await _enrich_ot(db, ot)
        return resp


# ── GET /api/ot/export/csv — §8.3.4 (must precede /{ot_id}) ─────────

@router.get("/export/csv")
async def export_ot_csv(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    status: str | None = Query(None),
    user_id: int | None = Query(None),
    shop_id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not ("SUPERVISOR" in roles or "ADMIN" in roles):
        raise APIError(403, "SUPERVISOR+ required for CSV export", "FORBIDDEN")

    q = select(OtRequest)

    # Role scoping
    if "ADMIN" not in roles:
        team_user_ids = (
            await db.execute(
                select(User.id).where(User.team == current_user.get("team"))
            )
        ).scalars().all()
        q = q.where(OtRequest.user_id.in_(team_user_ids))

    if status:
        q = q.where(OtRequest.status == status)
    if date_from:
        q = q.where(OtRequest.date >= date_from)
    if date_to:
        q = q.where(OtRequest.date <= date_to)
    if user_id and "ADMIN" in roles:
        q = q.where(OtRequest.user_id == user_id)

    # shop_id cross-filter: users with user_shop_access for that shop
    if shop_id:
        try:
            from sqlalchemy import text as sa_text
            shop_user_ids = (
                await db.execute(
                    sa_text("SELECT user_id FROM user_shop_access WHERE shop_id = :sid"),
                    {"sid": shop_id},
                )
            ).scalars().all()
            q = q.where(OtRequest.user_id.in_(shop_user_ids))
        except Exception:
            pass  # table may not exist yet (Branch 05/06)

    rows = (await db.execute(q.order_by(OtRequest.date, OtRequest.id))).scalars().all()

    # Pre-fetch user names
    user_ids_set: set[int] = set()
    for r in rows:
        user_ids_set.add(r.user_id)
        if r.submitted_by:
            user_ids_set.add(r.submitted_by)
    users_map: dict[int, User] = {}
    if user_ids_set:
        user_rows = (
            await db.execute(select(User).where(User.id.in_(user_ids_set)))
        ).scalars().all()
        users_map = {u.id: u for u in user_rows}

    # Pre-fetch approval records
    ot_ids = [r.id for r in rows]
    approvals_map: dict[int, list[OtApproval]] = {}
    if ot_ids:
        approval_rows = (
            await db.execute(
                select(OtApproval).where(OtApproval.ot_request_id.in_(ot_ids))
            )
        ).scalars().all()
        for a in approval_rows:
            approvals_map.setdefault(a.ot_request_id, []).append(a)
            if a.approver_id not in users_map:
                approver = (
                    await db.execute(select(User).where(User.id == a.approver_id))
                ).scalar_one_or_none()
                if approver:
                    users_map[approver.id] = approver

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ot_id", "user_name", "user_employee_no", "date", "start_time", "end_time",
        "minutes", "reason_code", "reason_text", "status",
        "submitted_by_name", "endorsed_by_name", "endorsed_at",
        "approved_by_name", "approved_at",
    ])

    for r in rows:
        user = users_map.get(r.user_id)
        sub_user = users_map.get(r.submitted_by) if r.submitted_by else None
        approvals = approvals_map.get(r.id, [])

        endorse_a = next((a for a in approvals if a.stage == "ENDORSE" and a.action == "APPROVE"), None)
        approve_a = next((a for a in approvals if a.stage == "APPROVE" and a.action == "APPROVE"), None)

        endorsed_by = users_map.get(endorse_a.approver_id) if endorse_a else None
        approved_by = users_map.get(approve_a.approver_id) if approve_a else None

        writer.writerow([
            r.id,
            user.name if user else "",
            user.employee_no if user else "",
            str(r.date),
            str(r.start_time),
            str(r.end_time),
            r.requested_minutes,
            r.reason_code,
            r.reason_text or "",
            r.status,
            sub_user.name if sub_user else "",
            endorsed_by.name if endorsed_by else "",
            str(endorse_a.acted_at) if endorse_a else "",
            approved_by.name if approved_by else "",
            str(approve_a.acted_at) if approve_a else "",
        ])

    csv_content = output.getvalue()
    date_suffix_from = date_from.strftime("%Y%m%d") if date_from else "start"
    date_suffix_to = date_to.strftime("%Y%m%d") if date_to else "end"
    filename = f"ot_export_{date_suffix_from}_{date_suffix_to}.csv"

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /api/ot — list §8.3.2 ──────────────────────────────────────

@router.get("", response_model=PaginatedResponse[OtResponse])
async def list_ot(
    paging: dict = Depends(pagination_params),
    status: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    user_id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(OtRequest)
    cq = select(func.count()).select_from(OtRequest)

    # Role-based scoping: WORKER sees only own, SUPERVISOR sees own team, ADMIN sees all
    roles = current_user.get("roles", [])
    if "ADMIN" not in roles:
        if "SUPERVISOR" in roles:
            # Can see own team users
            team_user_ids = (
                await db.execute(
                    select(User.id).where(User.team == current_user.get("team"))
                )
            ).scalars().all()
            q = q.where(OtRequest.user_id.in_(team_user_ids))
            cq = cq.where(OtRequest.user_id.in_(team_user_ids))
        else:
            # WORKER — own only
            q = q.where(OtRequest.user_id == current_user["user_id"])
            cq = cq.where(OtRequest.user_id == current_user["user_id"])

    # Filters
    if status:
        q = q.where(OtRequest.status == status)
        cq = cq.where(OtRequest.status == status)
    if date_from:
        q = q.where(OtRequest.date >= date_from)
        cq = cq.where(OtRequest.date >= date_from)
    if date_to:
        q = q.where(OtRequest.date <= date_to)
        cq = cq.where(OtRequest.date <= date_to)
    if user_id and ("SUPERVISOR" in roles or "ADMIN" in roles):
        q = q.where(OtRequest.user_id == user_id)
        cq = cq.where(OtRequest.user_id == user_id)

    total = (await db.execute(cq)).scalar()
    rows = (
        await db.execute(
            q.order_by(OtRequest.created_at.desc())
            .offset(paging["offset"])
            .limit(paging["per_page"])
        )
    ).scalars().all()

    items = [await _enrich_ot(db, r) for r in rows]
    return {
        "items": items,
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


# ── GET /api/ot/{id} — detail ──────────────────────────────────────

@router.get("/{ot_id}", response_model=OtResponse)
async def get_ot_detail(
    ot_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ot = (
        await db.execute(select(OtRequest).where(OtRequest.id == ot_id))
    ).scalar_one_or_none()
    if not ot:
        raise APIError(404, "OT request not found", "NOT_FOUND")

    return await _enrich_ot(db, ot)


# ── PATCH /api/ot/{id}/cancel — §7.1.3 ─────────────────────────────

@router.patch("/{ot_id}/cancel", response_model=OtResponse)
async def cancel_ot(
    ot_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ot = (
        await db.execute(select(OtRequest).where(OtRequest.id == ot_id))
    ).scalar_one_or_none()
    if not ot:
        raise APIError(404, "OT request not found", "NOT_FOUND")

    # Only the OT owner can cancel
    if ot.user_id != current_user["user_id"]:
        raise APIError(403, "Only the OT owner can cancel", "FORBIDDEN")

    # Only PENDING can be cancelled
    if ot.status != "PENDING":
        raise APIError(409, f"Cannot cancel OT in status '{ot.status}'", "INVALID_STATUS")

    before_status = ot.status
    ot.status = "CANCELLED"
    ot.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="ot_request",
        entity_id=ot.id,
        action="UPDATE",
        before={"status": before_status},
        after={"status": "CANCELLED"},
    )
    await db.commit()

    return await _enrich_ot(db, ot)


# ── POST /api/ot/{id}/endorse — §8.3.3 (SUPERVISOR) ───────────────

@router.post("/{ot_id}/endorse", response_model=OtApprovalResponse)
async def endorse_ot(
    ot_id: int,
    body: OtApprovalRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])

    # SUPERVISOR only — ADMIN cannot endorse
    if "SUPERVISOR" not in roles or "ADMIN" in roles:
        if "SUPERVISOR" not in roles:
            raise APIError(403, "Only SUPERVISOR can endorse", "FORBIDDEN")

    ot = (
        await db.execute(select(OtRequest).where(OtRequest.id == ot_id))
    ).scalar_one_or_none()
    if not ot:
        raise APIError(404, "OT request not found", "NOT_FOUND")

    # Status check — only PENDING
    if ot.status != "PENDING":
        raise APIError(409, f"Cannot endorse OT in status '{ot.status}'", "INVALID_STATUS")

    # Self-endorse check
    if ot.user_id == current_user["user_id"]:
        raise APIError(403, "Cannot endorse your own OT request", "SELF_ENDORSE")

    # Team check — supervisor can only endorse same team
    ot_user = (await db.execute(select(User).where(User.id == ot.user_id))).scalar_one_or_none()
    if ot_user and ot_user.team != current_user.get("team"):
        raise APIError(403, "Cannot endorse OT from a different team", "OT_WRONG_TEAM")

    # Validate action
    if body.action not in ("APPROVE", "REJECT"):
        raise APIError(422, "action must be APPROVE or REJECT", "VALIDATION_ERROR", field="action")

    new_status = "ENDORSED" if body.action == "APPROVE" else "REJECTED"
    ot.status = new_status
    ot.updated_at = datetime.now(timezone.utc)

    approval = OtApproval(
        ot_request_id=ot.id,
        approver_id=current_user["user_id"],
        stage="ENDORSE",
        action=body.action,
        comment=body.comment,
        acted_at=datetime.now(timezone.utc),
    )
    db.add(approval)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="ot_request",
        entity_id=ot.id,
        action="UPDATE",
        before={"status": "PENDING"},
        after={"status": new_status, "stage": "ENDORSE", "approval_action": body.action},
    )
    await db.commit()

    return {
        "ot_request_id": ot.id,
        "stage": "ENDORSE",
        "action": body.action,
        "approver_id": current_user["user_id"],
        "approver_name": current_user.get("display_name"),
        "comment": body.comment,
        "acted_at": approval.acted_at,
        "ot_request": await _enrich_ot(db, ot),
    }


# ── POST /api/ot/{id}/approve — §8.3.3a (ADMIN) ───────────────────

@router.post("/{ot_id}/approve", response_model=OtApprovalResponse)
async def approve_ot(
    ot_id: int,
    body: OtApprovalRequest,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    ot = (
        await db.execute(select(OtRequest).where(OtRequest.id == ot_id))
    ).scalar_one_or_none()
    if not ot:
        raise APIError(404, "OT request not found", "NOT_FOUND")

    # Status check — only ENDORSED
    if ot.status != "ENDORSED":
        raise APIError(409, f"Cannot approve OT in status '{ot.status}'", "INVALID_STATUS")

    # Self-approve check
    if ot.user_id == current_user["user_id"]:
        raise APIError(403, "Cannot approve your own OT request", "SELF_ENDORSE")

    if body.action not in ("APPROVE", "REJECT"):
        raise APIError(422, "action must be APPROVE or REJECT", "VALIDATION_ERROR", field="action")

    new_status = "APPROVED" if body.action == "APPROVE" else "REJECTED"
    ot.status = new_status
    ot.updated_at = datetime.now(timezone.utc)

    approval = OtApproval(
        ot_request_id=ot.id,
        approver_id=current_user["user_id"],
        stage="APPROVE",
        action=body.action,
        comment=body.comment,
        acted_at=datetime.now(timezone.utc),
    )
    db.add(approval)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="ot_request",
        entity_id=ot.id,
        action="UPDATE",
        before={"status": "ENDORSED"},
        after={"status": new_status, "stage": "APPROVE", "approval_action": body.action},
    )
    await db.commit()

    return {
        "ot_request_id": ot.id,
        "stage": "APPROVE",
        "action": body.action,
        "approver_id": current_user["user_id"],
        "approver_name": current_user.get("display_name"),
        "comment": body.comment,
        "acted_at": approval.acted_at,
        "ot_request": await _enrich_ot(db, ot),
    }
