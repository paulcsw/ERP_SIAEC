"""OT API tests (Branch 04 — commits 1-4)."""
import pytest
from tests.conftest import CSRF_HEADERS


TOMORROW = "2026-03-11"
OT_BASE = {
    "date": TOMORROW,
    "start_time": "18:00",
    "end_time": "20:00",
    "reason_code": "BACKLOG",
}


# ── Commit 1: Submit service ─────────────────────────────────────────


async def test_self_submit(async_client, db):
    """ADMIN self-submits OT — should succeed."""
    resp = await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == 1
    assert data["requested_minutes"] == 120
    assert data["status"] == "PENDING"


async def test_minutes_mismatch(async_client, db):
    """If requested_minutes doesn't match computed, 422."""
    body = {**OT_BASE, "requested_minutes": 999}
    resp = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 422


async def test_minutes_match(async_client, db):
    """If requested_minutes matches computed, OK."""
    body = {**OT_BASE, "requested_minutes": 120}
    resp = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["requested_minutes"] == 120


async def test_duplicate_ot(async_client, db):
    """Overlapping OT on same date → 422 DUPLICATE_OT."""
    await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    resp = await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["code"] == "DUPLICATE_OT"


async def test_monthly_limit(async_client, db):
    """Exceed 72h (4320 min) per month → 422 OT_MONTHLY_LIMIT_EXCEEDED."""
    # Submit many 10h blocks (600 min each) — 7 fills 4200, 8th → 4800 > 4320
    for i in range(7):
        body = {
            "date": f"2026-03-{11 + i:02d}",
            "start_time": "08:00",
            "end_time": "18:00",
            "reason_code": "AOG",
        }
        r = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
        assert r.status_code == 200, f"Request {i+1} failed: {r.json()}"

    body = {
        "date": "2026-03-20",
        "start_time": "08:00",
        "end_time": "18:00",
        "reason_code": "AOG",
    }
    resp = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["code"] == "OT_MONTHLY_LIMIT_EXCEEDED"


# ── Bulk submit ──────────────────────────────────────────────────────


async def test_bulk_submit(async_client, db):
    """ADMIN submits OT for multiple users."""
    body = {**OT_BASE, "user_ids": [2, 3]}
    resp = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["created_count"] == 2
    assert data["skipped_count"] == 0


async def test_bulk_skip_duplicate(async_client, db):
    """Duplicate user in bulk → skipped."""
    body = {**OT_BASE, "user_ids": [3]}
    await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    resp = await async_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped_count"] == 1
    assert data["skipped"][0]["reason"] == "DUPLICATE_DATE"


async def test_worker_cannot_bulk(worker_client, db):
    """WORKER cannot submit for others."""
    body = {**OT_BASE, "user_ids": [1]}
    resp = await worker_client.post("/api/ot", json=body, headers=CSRF_HEADERS)
    assert resp.status_code == 403


# ── Commit 2: List / Detail / Cancel ────────────────────────────────


async def test_list_ot(async_client, db):
    """Admin can list OT requests."""
    await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    resp = await async_client.get("/api/ot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert "items" in data


async def test_ot_detail(async_client, db):
    """Get OT by ID."""
    create = await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    ot_id = create.json()["id"]
    resp = await async_client.get(f"/api/ot/{ot_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == ot_id


async def test_cancel_pending(async_client, db):
    """Owner can cancel PENDING OT."""
    create = await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    ot_id = create.json()["id"]
    resp = await async_client.patch(f"/api/ot/{ot_id}/cancel", headers=CSRF_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_cancel_not_owner(sup_client, async_client, db):
    """Non-owner cannot cancel."""
    create = await async_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    ot_id = create.json()["id"]
    resp = await sup_client.patch(f"/api/ot/{ot_id}/cancel", headers=CSRF_HEADERS)
    assert resp.status_code == 403


# ── Commit 3: Endorse / Approve ─────────────────────────────────────


async def _create_worker_ot(worker_client):
    """Helper: worker submits OT, returns id."""
    resp = await worker_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    assert resp.status_code == 200, resp.json()
    return resp.json()["id"]


async def test_supervisor_endorse(sup_client, worker_client, db):
    """SUPERVISOR endorses worker's PENDING OT."""
    ot_id = await _create_worker_ot(worker_client)
    resp = await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "ENDORSE"
    assert data["ot_request"]["status"] == "ENDORSED"


async def test_supervisor_reject(sup_client, worker_client, db):
    """SUPERVISOR rejects worker's OT."""
    ot_id = await _create_worker_ot(worker_client)
    resp = await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "REJECT", "comment": "Not justified"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["ot_request"]["status"] == "REJECTED"


async def test_self_endorse_forbidden(sup_client, db):
    """SUPERVISOR cannot endorse own OT."""
    # Supervisor self-submits
    resp = await sup_client.post("/api/ot", json=OT_BASE, headers=CSRF_HEADERS)
    ot_id = resp.json()["id"]
    resp = await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "SELF_ENDORSE"


async def test_admin_cannot_endorse(async_client, worker_client, db):
    """ADMIN cannot endorse (§7.1.2 rule 2)."""
    ot_id = await _create_worker_ot(worker_client)
    resp = await async_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 403


async def test_full_2stage_approval(sup_client, async_client, worker_client, db):
    """PENDING → ENDORSED (supervisor) → APPROVED (admin)."""
    ot_id = await _create_worker_ot(worker_client)

    # Stage 1: endorse
    r1 = await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert r1.status_code == 200
    assert r1.json()["ot_request"]["status"] == "ENDORSED"

    # Stage 2: approve
    r2 = await async_client.post(
        f"/api/ot/{ot_id}/approve",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert r2.status_code == 200
    assert r2.json()["ot_request"]["status"] == "APPROVED"


async def test_admin_approve_pending_fails(async_client, worker_client, db):
    """Cannot approve PENDING (must be ENDORSED first)."""
    ot_id = await _create_worker_ot(worker_client)
    resp = await async_client.post(
        f"/api/ot/{ot_id}/approve",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATUS"


async def test_cancel_endorsed_fails(sup_client, worker_client, db):
    """Cannot cancel ENDORSED OT (only PENDING allowed)."""
    ot_id = await _create_worker_ot(worker_client)

    # Endorse it first
    await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )

    # Worker tries to cancel
    resp = await worker_client.patch(f"/api/ot/{ot_id}/cancel", headers=CSRF_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATUS"


# ── Commit 4: CSV Export ─────────────────────────────────────────────


async def test_csv_export(async_client, worker_client, sup_client, db):
    """CSV export returns proper headers and content."""
    # Create data: worker submits, supervisor endorses, admin approves
    ot_id = await _create_worker_ot(worker_client)
    await sup_client.post(
        f"/api/ot/{ot_id}/endorse",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )
    await async_client.post(
        f"/api/ot/{ot_id}/approve",
        json={"action": "APPROVE"},
        headers=CSRF_HEADERS,
    )

    resp = await async_client.get("/api/ot/export/csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]

    lines = resp.text.strip().split("\n")
    assert len(lines) >= 2  # header + at least 1 data row
    header = lines[0]
    assert "ot_id" in header
    assert "user_name" in header
    assert "endorsed_by_name" in header
    assert "approved_by_name" in header


async def test_csv_export_worker_forbidden(worker_client, db):
    """WORKER cannot export CSV."""
    resp = await worker_client.get("/api/ot/export/csv")
    assert resp.status_code == 403
