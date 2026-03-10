"""Tests for Reference Data CRUD + CSV Import (Branch 03, §8.6)."""
import io

from tests.conftest import CSRF_HEADERS


# ═══════════════════════════════════════════════════════════════════
# Aircraft
# ═══════════════════════════════════════════════════════════════════

async def test_create_and_list_aircraft(async_client):
    resp = await async_client.post(
        "/api/aircraft",
        json={"ac_reg": "9V-SMA", "airline": "Singapore Airlines"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ac_reg"] == "9V-SMA"
    assert body["status"] == "ACTIVE"

    # List
    resp2 = await async_client.get("/api/aircraft")
    assert resp2.status_code == 200
    assert resp2.json()["total"] >= 1


async def test_create_aircraft_dup(async_client):
    await async_client.post(
        "/api/aircraft",
        json={"ac_reg": "9V-DUP"},
        headers=CSRF_HEADERS,
    )
    resp = await async_client.post(
        "/api/aircraft",
        json={"ac_reg": "9V-DUP"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "ac_reg"


async def test_update_aircraft(async_client):
    create = await async_client.post(
        "/api/aircraft",
        json={"ac_reg": "9V-UPD"},
        headers=CSRF_HEADERS,
    )
    aid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/aircraft/{aid}",
        json={"airline": "Scoot", "status": "ON_HOLD"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["airline"] == "Scoot"
    assert resp.json()["status"] == "ON_HOLD"


# ═══════════════════════════════════════════════════════════════════
# Work Packages
# ═══════════════════════════════════════════════════════════════════

async def test_create_and_list_work_packages(async_client):
    ac = await async_client.post(
        "/api/aircraft", json={"ac_reg": "9V-WP1"}, headers=CSRF_HEADERS
    )
    ac_id = ac.json()["id"]

    resp = await async_client.post(
        "/api/work-packages",
        json={
            "aircraft_id": ac_id,
            "rfo_no": "1200000101",
            "title": "C-Check Q1",
        },
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["rfo_no"] == "1200000101"

    # List with filter
    resp2 = await async_client.get(f"/api/work-packages?aircraft_id={ac_id}")
    assert resp2.status_code == 200
    assert resp2.json()["total"] >= 1


async def test_create_wp_bad_aircraft(async_client):
    resp = await async_client.post(
        "/api/work-packages",
        json={"aircraft_id": 9999, "title": "Bad"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "aircraft_id"


# ═══════════════════════════════════════════════════════════════════
# Shop Streams
# ═══════════════════════════════════════════════════════════════════

async def test_create_and_list_shop_streams(async_client):
    ac = await async_client.post(
        "/api/aircraft", json={"ac_reg": "9V-SS1"}, headers=CSRF_HEADERS
    )
    wp = await async_client.post(
        "/api/work-packages",
        json={"aircraft_id": ac.json()["id"], "title": "WP-SS"},
        headers=CSRF_HEADERS,
    )
    wp_id = wp.json()["id"]

    resp = await async_client.post(
        "/api/shop-streams",
        json={"work_package_id": wp_id, "shop_code": "SM"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["shop_code"] == "SM"

    resp2 = await async_client.get(f"/api/shop-streams?work_package_id={wp_id}")
    assert resp2.status_code == 200
    assert resp2.json()["total"] >= 1


# ═══════════════════════════════════════════════════════════════════
# CSV Import (§8.6.3)
# ═══════════════════════════════════════════════════════════════════

async def test_csv_import_aircraft(async_client):
    csv_content = "ac_reg,airline\n9V-CSV1,SIA\n9V-CSV2,Scoot\n"
    resp = await async_client.post(
        "/api/reference/import/csv?entity_type=aircraft",
        files={"file": ("aircraft.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_type"] == "aircraft"
    assert body["created_count"] == 2
    assert body["skipped_count"] == 0


async def test_csv_import_aircraft_skip_dup(async_client):
    # Create one first
    await async_client.post(
        "/api/aircraft", json={"ac_reg": "9V-SKIP"}, headers=CSRF_HEADERS
    )

    csv_content = "ac_reg,airline\n9V-SKIP,SIA\n9V-NEW1,Scoot\n"
    resp = await async_client.post(
        "/api/reference/import/csv?entity_type=aircraft",
        files={"file": ("a.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        headers=CSRF_HEADERS,
    )
    body = resp.json()
    assert body["created_count"] == 1
    assert body["skipped_count"] == 1


async def test_csv_import_work_package(async_client):
    # Prereq: create aircraft
    await async_client.post(
        "/api/aircraft", json={"ac_reg": "9V-CSVWP"}, headers=CSRF_HEADERS
    )

    csv_content = "aircraft_ac_reg,title,rfo_no\n9V-CSVWP,C-Check,RF001\n"
    resp = await async_client.post(
        "/api/reference/import/csv?entity_type=work_package",
        files={"file": ("wp.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        headers=CSRF_HEADERS,
    )
    body = resp.json()
    assert body["created_count"] == 1
    assert body["errors"] == []


async def test_csv_import_bad_entity_type(async_client):
    resp = await async_client.post(
        "/api/reference/import/csv?entity_type=invalid",
        files={"file": ("x.csv", io.BytesIO(b"a\nb\n"), "text/csv")},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
