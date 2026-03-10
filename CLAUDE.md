# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CIS ERP system for an MRO (Maintenance, Repair, Overhaul) aviation facility in Singapore. Combines **OT (overtime) tracking with 2-stage approval** and a **Task Manager (weekly snapshot-based work management)** into a single system. The project is in early development — the SSOT design documents exist but application code has not yet been scaffolded.

## Tech Stack

- **Language**: Python 3.12+
- **Web Framework**: FastAPI (REST API + SSR endpoints)
- **UI**: Jinja2 templates + HTMX (server-rendered, minimal JS), Tailwind CSS (CDN)
- **Database**: Microsoft SQL Server (ODBC Driver 18 + aioodbc async / pyodbc sync)
- **ORM**: SQLAlchemy 2.x (async), Alembic for migrations
- **Validation**: Pydantic v2
- **Auth**: Azure AD OAuth2 (Authorization Code Flow), server-side sessions (8h default)
- **Testing**: pytest + httpx

## Key Commands

```bash
# Activate virtual environment
source venv/Scripts/activate   # Windows Git Bash

# Start MSSQL dev database (Docker)
docker compose up -d db

# Run the app (once scaffolded)
uvicorn app.main:app --reload

# Run Alembic migrations
alembic upgrade head

# Run tests
pytest
pytest tests/test_ot.py -k "test_name"   # single test

# Seed dev data
python scripts/seed_data.py
```

## Architecture

### SSOT-Driven Development

All implementation must conform to the SSOT document in `docs/`. The current versions are:
- **SSOT**: `docs/ERP_Integrated_SSOT_v2_0_Merged_2026-03-10_MiniPatch1-12b-fix2.md`
- **Implementation Plan**: `docs/ERP_SSOT_v2_Implementation_Plan_v2_MiniPatch1-12b-fix2_Aligned_2026-03-10.md`

**The SSOT is the single source of truth** — do not deviate from its schema, business rules, or API contracts. The implementation plan defines the branch/commit order (12 branches, 00–11).

### Implementation Branch Order

| # | Branch | Purpose |
|--:|--------|---------|
| 00 | `chore/bootstrap-app` | Project skeleton, Docker, ODBC18 |
| 01 | `feat/db-001-core-ot-rfo` | Alembic 001: core + OT + audit + system_config |
| 02 | `feat/security-auth-csrf-pagination` | Azure AD OAuth2, CSRF, pagination, rate limit |
| 03 | `feat/admin-users-reference-config-import` | Users + Reference CRUD + CSV import |
| 04 | `feat/ot-end-to-end-2stage` | OT full slice + 72h limit + mobile |
| 05 | `feat/db-002-task-schema-distribution` | Alembic 002: task schema |
| 06 | `feat/task-admin-shop-access` | Shops + user_shop_access CRUD |
| 07 | `feat/task-core-snapshots-rfo` | Task Core API + optimistic lock + MH policy |
| 08 | `feat/task-lifecycle-batch` | init-week + batch + delete/restore |
| 09 | `feat/task-distribution-ui` | Task Manager/Data Entry UI + mobile shell |
| 10 | `feat/reporting-views-sql-expanded` | SQL Server reporting views |
| 11 | `feat/stats-rfo-dashboard` | OT stats + RFO dashboard |

### Target Project Structure

```
app/
├── main.py                 # FastAPI app factory
├── config.py               # Pydantic BaseSettings
├── database.py             # SQLAlchemy async engine + session
├── middleware/              # CSRF (Double Submit Cookie), rate limiting (slowapi)
├── models/                 # SQLAlchemy ORM (user, reference, ot, task, audit, system_config)
├── schemas/                # Pydantic request/response models
├── api/                    # FastAPI routers — JSON endpoints
│   └── deps.py             # Auth, RBAC, shop_access dependencies
├── views/                  # HTMX server-rendered views (Jinja2)
├── services/               # Business logic layer
├── templates/              # Jinja2 HTML (base, components, dashboard, ot, tasks, admin, stats)
└── static/
```

### Key Architectural Patterns

- **3-layer separation**: API routers → Services (business logic) → Models/DB. Routers must not contain business logic.
- **RBAC**: Three roles — WORKER, SUPERVISOR, ADMIN. Enforced via FastAPI dependencies (`api/deps.py`).
- **Shop-scoped access**: Non-ADMIN users access Task features through `user_shop_access` (VIEW/EDIT/MANAGE). ADMIN bypasses all shop access checks.
- **Optimistic locking**: `task_snapshots.version` column — increment on update, reject on mismatch (409 CONFLICT_VERSION).
- **Audit trail**: All write operations log to `audit_logs` table (entity_type, entity_id, action, before_json, after_json).
- **Pagination wrapper**: All list endpoints return `{ items, total, page, per_page }`.

## Critical Business Rules

### OT (Overtime)
- **2-stage approval**: PENDING → ENDORSED (by SUPERVISOR, same team) → APPROVED (by ADMIN). Self-endorse/approve forbidden.
- **Monthly 72h limit** (4,320 min): Sum of APPROVED+PENDING+ENDORSED minutes per calendar month. Exceeding blocks submission (422 OT_MONTHLY_LIMIT_EXCEEDED).
- Only PENDING OT can be cancelled, and only by the owner.

### Task Manager
- **Weekly snapshots**: `task_items` (stable entity) + `task_snapshots` (per meeting_date state). `mh_incurred_hours` is **cumulative**, not weekly delta.
- **Carry-over (init-week)**: Copies non-COMPLETED, non-deleted, active task snapshots to new meeting_date. Idempotent via UNIQUE(task_id, meeting_date).
- **MH decrease rules**: EDIT permission → forbidden (422). MANAGE permission → allowed only with `correction_reason`.
- **Soft delete**: `is_deleted` flag on snapshots, `is_active` flag on task_items.
- **Data Entry RBAC boundary**: Init-week and soft delete are Task Manager-only operations, not available in Data Entry.

## DB Conventions (MSSQL-specific)

- Enums: `NVARCHAR` columns with `CHECK` constraints (no PostgreSQL-style CREATE TYPE)
- JSON columns: `NVARCHAR(MAX)` storing JSON strings
- Timestamps: `DATETIMEOFFSET` with `GETUTCDATE()` defaults
- Booleans: `BIT` (0/1)
- Filtered unique indexes for nullable unique columns (e.g., `WHERE email IS NOT NULL`)

## Error Format

All API errors follow: `{ "detail": "...", "code": "MACHINE_READABLE_CODE" }`. Key codes: AUTH_REQUIRED (401), FORBIDDEN/CSRF_INVALID/SHOP_ACCESS_DENIED (403), CONFLICT_VERSION/INVALID_STATUS (409), VALIDATION_ERROR/DUPLICATE_OT/OT_MONTHLY_LIMIT_EXCEEDED/MH_DECREASE_FORBIDDEN (422), RATE_LIMIT (429).

## Environment Variables

Required: `DATABASE_URL`, `SECRET_KEY`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_REDIRECT_URI`

## Other Files

- `erp_ui_v3_patch12.html` — Static UI prototype (Tailwind + vanilla JS). Reference for visual design, not production code.
- `docker-compose.yml` — MSSQL 2022 dev database on port 1433 (SA password: `YourStrong!Passw0rd`).
