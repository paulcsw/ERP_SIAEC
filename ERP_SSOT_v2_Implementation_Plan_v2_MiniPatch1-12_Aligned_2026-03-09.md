# ERP Implementation Plan v2 (SSOT v2.0 MiniPatch 1~12 Aligned)

- 기준 SSOT: **ERP 통합 SSOT v2.0 (MiniPatch 1~12 Applied)** — 2026-03-09 (Asia/Singapore)
- 기준 Plan: `ERP_SSOT_v2_Implementation_Plan_v2_MiniPatch1-10_Aligned_2026-03-03.md`
- 본 문서 목적: 위 SSOT 기준으로 **브랜치/커밋 단위 실행 플랜을 MiniPatch 11~12 변경사항까지 포함해 재정렬/수정**한다.

> 핵심 변경점(이번 정합화에서 반영)
> - OT: **2단계 승인(SUPERVISOR endorse → ADMIN approve)**, **월 72h 한도(4,320분)**, **Admin OT Approve 대기열** 반영
> - Reference/RFO: **`work_packages.rfo_no`**, **Reference CSV Import**, **`/api/rfo/{work_package_id}/summary`** 반영
> - Task: **`assigned_supervisor_id` / `assigned_worker_id` / `distributed_at` / `planned_mh` / `supervisor_updated_at`** 반영, Task Distribution API 추가
> - UI: **`/tasks/meeting` → `/tasks`(Task Manager)**, **Data Entry 역할 재정의**, **`/rfo/{id}`** 신규, **OT Stats 확장**
> - Reporting: 기존 OT/Task views 확장 + **`vw_fact_ot_by_reason` / `vw_fact_ot_weekly` / `vw_rfo_efficiency` / `vw_rfo_burndown` / `vw_task_distribution`** 추가

---

## 0. 적용 범위 / MVP 정의

### 0.1 포함(MVP)
- **OT**
  - submit(본인/대리/벌크), list/detail, cancel
  - 2단계 승인: SUPERVISOR endorse, ADMIN approve/reject
  - 월 72h 한도 검증(단건 차단 + 벌크 per-user skip)
  - OT CSV export, Admin OT Approve 대기열, OT 통계(요약/월간 사용량/사유별/주간 추세)
- **Task / RFO**
  - shops + user_shop_access 기반 권한
  - `task_items` + weekly `task_snapshots`
  - `work_package_id` 기반 RFO 연결, Task Distribution, Worker Assignment(1:1 FK), Planned MH
  - carry-over(init-week), snapshot CRUD + optimistic locking + MH 규칙
  - batch update(all-or-nothing), soft delete/restore, deactivate/reactivate
  - Task Import Preview/Confirm, Assign/Bulk Assign, Assign Worker
  - Task Manager / Data Entry / Task Detail / RFO Detail / Task CSV export
- **Admin / Reference**
  - users CRUD(조건부 HARD DELETE 포함)
  - aircraft / work_packages(rfo_no 포함) / shop_streams CRUD
  - Reference CSV Import, shops CRUD, shop access 관리
  - system settings(system_config) UI + `/api/config`
- **공통**
  - RBAC 강제, CSRF, pagination wrapper, rate limiting, audit_logs
  - SQL Server reporting views → Power BI

### 0.2 제외(Phase 2+)
- Teams/Outlook 실제 연동(웹훅/메일 발송): **UI 토글 + 설정 저장만** 제공
- future tables(shift/attendance/worklog/ledger) 실제 기능 구현: **DDL만 동봉**
- SAP 실시간 동기화(API 연동): 현재는 **수동 입력 / CSV / Excel import** 기준
- 다중 Worker 배정(M:N): MVP는 **`assigned_worker_id` 1:1**로 고정, 확장은 MiniPatch 13+
- HasIssue 자동 계산 로직 고도화(규칙 기반): Phase 2
- Celery/Redis 기반 비동기 잡: Phase 2

---

## 1. 브랜치/커밋 단위 개발 플랜

### 1.0 전체 로드맵

| 순서 | 브랜치 | 목표 | SSR 포함 |
|---:|---|---|:---:|
| 00 | `chore/bootstrap-app` | 프로젝트 구조/런타임/테스트 프레임 + Docker(ODBC18) + import 의존성 | — |
| 01 | `feat/db-001-core-ot-rfo` | Alembic 001: core + OT(2단계) + audit + system_config + rfo_no + future schema(DDL) | — |
| 02 | `feat/security-auth-csrf-pagination` | Azure AD OAuth2 + session, CSRF, pagination, rate limit | — |
| 03 | `feat/admin-users-reference-config-import` | Users + Reference CRUD(UI/API) + `/api/config` + Reference CSV import | ✅ |
| 04 | `feat/ot-end-to-end-2stage` | OT 수직 슬라이스(SSR 포함) + 72h limit + CSV export + Admin approve queue | ✅ |
| 05 | `feat/db-002-task-schema-distribution` | Alembic 002: shops + user_shop_access + task schema + distribution fields | — |
| 06 | `feat/task-admin-shop-access` | shops + user_shop_access CRUD(UI/API) + access service + OT export shop_id 교차필터 | ✅ |
| 07 | `feat/task-core-snapshots-rfo` | Task Core API + optimistic lock + MH 정책 + RFO/airline/supervisor 필터 | — |
| 08 | `feat/task-lifecycle-batch` | init-week + batch(all-or-nothing) + delete/restore + deactivate/reactivate | — |
| 09 | `feat/task-distribution-ui` | Task Manager/Data Entry/Task Detail + Task Distribution API + Task CSV export + Settings | ✅ |
| 10 | `feat/reporting-views-sql-expanded` | SQL Server reporting views(create_views.py + 선택적 migration) 확장 | — |
| 11 | `feat/stats-rfo-dashboard` | OT 통계 확장 + RFO summary/metrics API + OT/RFO 대시보드 | ✅ |

---

## 2. 공통 구현 규칙(브랜치 공통 DoD)

### 2.1 인증/권한
- `/login` → Azure AD redirect → `/auth/callback`에서 세션 생성
- `/api/**`는 기본적으로 **인증 필수(401)**. 예외: `/health`, `/login`, `/auth/callback`, `/logout`
- RBAC 기본 역할:
  - WORKER / SUPERVISOR / ADMIN (`role_name`)
- OT 승인 스코프:
  - **SUPERVISOR만** `POST /api/ot/{id}/endorse`
  - **ADMIN만** `POST /api/ot/{id}/approve`
- Task 스코프:
  - 비ADMIN은 `user_shop_access`(VIEW/EDIT/MANAGE) 기준으로 shop 단위 권한 판정
  - ADMIN은 `user_shop_access` 행 없이도 **모든 shop bypass**
- 화면 역할:
  - `/tasks`(Task Manager): **ADMIN 주사용**, SUPERVISOR는 본인 shop 범위 읽기전용
  - `/tasks/entry`(Data Entry): **SUPERVISOR 워크스테이션**, 배포된 task 기준 동작

### 2.2 에러 포맷(전역)
- 모든 에러는 `{ "detail": "...", "code": "..." }` 형태
- 401: `AUTH_REQUIRED`
- 403: `FORBIDDEN`, `SHOP_ACCESS_DENIED`, `SELF_ENDORSE`, `OT_WRONG_TEAM`, `CSRF_INVALID`
- 409: optimistic locking(`CONFLICT_VERSION`) / 상태충돌(`INVALID_STATUS`)
- 422: 검증 실패(`VALIDATION_ERROR` 계열), `DUPLICATE_OT`, `OT_MONTHLY_LIMIT_EXCEEDED`, `USER_HAS_REFERENCES`, `BATCH_VALIDATION_ERROR`, `MH_DECREASE_FORBIDDEN`, `CORRECTION_REASON_REQUIRED`
- 429: `RATE_LIMIT`

### 2.3 Audit logs(전역)
- 모든 write(Create/Update/Delete/Restore/Deactivate/Reactivate/Endorse/Approve/Import/Assign)는 `audit_logs`에 기록
- entity_type 예시:
  - `ot_request`, `ot_approval`, `task_item`, `task_snapshot`, `shop`, `user_shop_access`, `user`, `system_config`
- batch update는 **snapshot별 1행 audit** 생성
- import preview는 audit 대상 아님, **confirm 시점**에만 audit 생성

### 2.4 DB 구현(중요)
- DB는 **MSSQL/Azure SQL** 기준
- PostgreSQL enum 대신: **NVARCHAR + CHECK constraint** 로 enum 구현
- JSON은 `NVARCHAR(MAX)`(JSON 문자열)로 저장
- Greenfield 기준:
  - Alembic **001**은 MiniPatch 11까지 반영된 core/OT/RFO 스키마를 포함
  - Alembic **002**는 MiniPatch 12까지 반영된 task/distribution 스키마를 포함
- 이미 001/002가 배포된 repo라면:
  - **기존 migration을 rewrite하지 말고 additive revision**으로 분리한다

### 2.5 업로드 / import 공통 규칙
- file upload는 `multipart/form-data`
- 허용 파일: `.csv`, `.xlsx`
- 권장 size limit: **5MB**
- CSV는 UTF-8 기준, Excel은 `openpyxl` 기반으로 파싱
- Preview/Confirm 플로우가 필요한 import는 **미리보기 응답과 실제 저장을 분리**한다

---

## 3. 브랜치 상세

### Branch 00 — `chore/bootstrap-app`
**목표**: SSOT 프로젝트 구조 + Docker 실행 + MSSQL(ODBC18) 연결 가능한 런타임 뼈대

**커밋**
1) `chore: init fastapi project skeleton + healthcheck + pytest`
- app factory, settings, async DB session
- `/health`(또는 `/api/health`) + smoke test

2) `chore(docker): docker-compose app container + env template (DB external MSSQL)`
- docker-compose: app 서비스만. DB는 사내 MSSQL/Azure SQL 사용
- `.env.example`: `DATABASE_URL`, `SECRET_KEY`, Azure 변수들

3) `chore(docker): Dockerfile install ODBC Driver 18 (sqlserver)`
- `python:3.12-slim` 기반
- `msodbcsql18` 설치

4) `chore(deps): base dependencies for SSR/upload/import`
- `fastapi`, `sqlalchemy`, `aioodbc`, `alembic`, `pydantic`, `jinja2`, `httpx`, `pytest`
- `python-multipart`, `openpyxl`, `slowapi`(또는 동등 라이브러리)
- Tailwind CDN / HTMX / Chart.js를 base template에서 사용 가능하게 구성

**DoD**
- `docker compose up`으로 app 기동
- MSSQL 연결 성공(health check에서 DB ping)
- pytest smoke 통과
- 업로드 관련 dependency import 오류 없음

---

### Branch 01 — `feat/db-001-core-ot-rfo`
**목표**: Alembic 001로 core + OT + audit + system_config + RFO 확장을 구축한다.

**커밋**
1) `feat(alembic): 001 core + OT + audit + system_config + future schema (MSSQL, MiniPatch 11 baked-in)`
- users / roles / user_roles
  - users에 `azure_oid` 포함(비밀번호 컬럼 없음)
  - `email`, `employee_no` unique index(필요 시 filtered unique)
- reference: aircraft / work_packages / shop_streams
  - `work_packages.rfo_no NVARCHAR(50) NULL`
  - filtered unique index: `uq_wp_rfo_no_not_null`
- OT: `ot_requests` / `ot_approvals`
  - `ot_requests.status` CHECK에 `ENDORSED` 포함
  - `submitted_by` 포함(대리/벌크 제출 추적)
  - `requested_minutes`는 optional(서버 계산값 저장)
  - `ot_approvals.stage` (`ENDORSE`/`APPROVE`) + `idx_ota_stage`
- audit: `audit_logs`
- config: `system_config(key/value)`
- future tables: DDL만 포함(Phase 2/3, 실제 기능 X)
- enum 구현: NVARCHAR 컬럼 + CHECK constraints

2) `feat(models): SQLAlchemy models for core + reference + OT + audit + config`
- app/models/*.py 정리(SSOT 구조 준수)

3) `chore(seed): seed_data.py (roles/users/reference/system_config)`
- roles/users seed
- work_packages seed에 `rfo_no` 포함
- system_config 기본 key들 seed:
  - `meeting_current_date`, `meeting_auto_advance`
  - Teams/Outlook/critical alert 관련 토글/수신자/템플릿

4) `test(db): alembic upgrade/downgrade smoke (MSSQL)`
- 빈 DB에서 `upgrade head` → `downgrade -1` → 재 `upgrade`

**DoD**
- `alembic upgrade head` 성공(MSSQL)
- `rfo_no`, `ENDORSED`, `stage`가 스키마에 반영됨
- filtered unique index / CHECK constraints 포함해 스키마 일치
- seed 스크립트 1회 실행 시 기준 데이터 생성
- downgrade/재upgrade smoke 통과
- 주의: `users.employee_no`, `system_config.[key]`에 UNIQUE 제약이 있으므로 별도 중복 인덱스 생성 금지

---

### Branch 02 — `feat/security-auth-csrf-pagination`
**목표**: Azure AD OAuth2 + session 인증 강제, CSRF, pagination wrapper, rate limiting 전역 적용

**커밋**
1) `feat(auth): Azure AD OAuth2 login/callback/logout + session`
- GET `/login` (Azure authorize endpoint redirect)
- GET `/auth/callback` (code → token 교환 후 세션 생성)
- POST `/logout` (세션 삭제)
- GET `/api/auth/me` (현재 사용자 + 역할)
- 사용자 매칭 규칙:
  - `users.azure_oid` 우선
  - 없으면 email 매칭 후 `azure_oid` 자동 매핑
  - 미등록이면 403 `USER_NOT_REGISTERED`

2) `feat(api): global auth dependencies (get_current_user, require_role)`
- `/api` 라우터 기본 인증 강제
- 예외 라우트는 allowlist

3) `feat(middleware): CSRF double submit cookie`
- 구현 4단계:
  1. 로그인 성공 시: 랜덤 CSRF 토큰(32byte hex) → 세션 저장 + `Set-Cookie(csrftoken, HttpOnly=false, SameSite=Lax)`
  2. `base.html`: `<body hx-headers='js:{"X-CSRFToken": document.cookie.match(/csrftoken=([^;]+)/)?.[1] || ""}'>`
  3. 미들웨어: POST/PATCH/DELETE 시 `X-CSRFToken` 헤더 == `session["csrf_token"]` 검증
  4. 세션 갱신 시 CSRF 토큰도 재생성 (session fixation 방지)
- 불일치/누락 → 403 `CSRF_INVALID`

4) `feat(api): pagination response wrapper`
- `{items,total,page,per_page}` 형식 고정

5) `feat(middleware): rate limiting`
- 기본 120/min
- key 정책: 인증되면 `user_id` 기준, 미인증이면 IP 기준
- 429 `RATE_LIMIT`

**DoD**
- 미로그인 상태 `/api/*`는 401
- CSRF 누락 시 403
- rate limit 초과 시 429
- `/api/auth/me`가 세션 기반으로 동작

---

### Branch 03 — `feat/admin-users-reference-config-import`
**목표**: Admin CRUD + Reference CRUD/import + `/api/config` + Admin UI를 구축한다.

**커밋**
1) `feat(api): users CRUD + conditional HARD DELETE (ADMIN)`
- GET/POST/PATCH `/api/users`
- PATCH에서 roles/team/is_active/employee_no/email 업데이트
- DELETE `/api/users/{id}`
  - 참조 검사: `audit_logs.actor_id`, `ot_requests.user_id`, `ot_requests.submitted_by`, `ot_approvals.approver_id`, `task_snapshots.last_updated_by/deleted_by`
  - 참조 존재 시 422 `USER_HAS_REFERENCES`, 없으면 물리 삭제(200)

2) `feat(api): reference CRUD (aircraft/work-packages/shop-streams)`
- GET `/api/aircraft` (WORKER+), POST(ADMIN)
- GET `/api/work-packages` (WORKER+), POST(ADMIN)
  - filter: `aircraft_id`, `rfo_no`
  - POST payload에 `rfo_no` 허용
- GET `/api/shop-streams` (WORKER+), POST(ADMIN)

3) `feat(api): POST /api/reference/import/csv`
- Auth: ADMIN
- multipart upload + 5MB limit
- 지원 대상:
  - `aircraft`
  - `work_package`
  - `shop_stream`
- 규칙:
  - 중복(`rfo_no`, `ac_reg` 등)은 skip
  - FK 실패는 row-level error로 수집
  - 성공 건만 audit_logs 기록

4) `feat(api): system config endpoints (/api/config)`
- GET `/api/config` (ADMIN)
- PATCH `/api/config` (ADMIN, batch update)
- GET `/api/config/{key}`
  - ADMIN: 전 key
  - SUPERVISOR+: `meeting_current_date`만 허용
- config 변경은 `audit_logs(entity_type=system_config)` 기록

5) `feat(ssr): /admin/users + /admin/reference`
- `/admin/users`
  - Add User 모달
  - Edit User 모달
  - activate/deactivate / hard-delete 분기 UX
- `/admin/reference`
  - Aircraft / RFO / Shop Stream 탭
  - CSV Import 액션 + 결과 토스트

**DoD**
- users hard delete 조건부 동작
- work_packages `rfo_no` CRUD / filter 동작
- reference CSV import의 skip/error 처리 동작
- `/api/config` 권한/감사 로그 동작
- `/admin/users`, `/admin/reference`에서 핵심 CRUD 가능

---

### Branch 04 — `feat/ot-end-to-end-2stage`
**목표**: OT 기능을 API+SSR까지 수직 슬라이스로 완성한다.

**커밋**
1) `feat(ot): submit service (self/proxy/bulk) + minutes compute + monthly limit`
- `requested_minutes` 미제공 시 `(end-start)`로 계산하여 저장
- 제공 시 계산값과 불일치하면 422 `VALIDATION_ERROR` (`field=requested_minutes`)
- 단건 중복(동일 user/date 시간 겹침) → 422 `DUPLICATE_OT`
- 월 72h 한도(4,320분):
  - 단건 초과 → 422 `OT_MONTHLY_LIMIT_EXCEEDED`
  - 벌크 초과 → 해당 user만 skip(`MONTHLY_LIMIT_EXCEEDED`)
- `submitted_by`: 대리/벌크는 요청자(supervisor/admin), 본인은 self

2) `feat(api): POST/GET/PATCH /api/ot`
- POST `/api/ot`
  - `user_ids` 없으면 본인 신청
  - SUPERVISOR+/ADMIN만 `user_ids` 사용 가능
  - team 검증: supervisor는 본인 team 사용자만
- GET `/api/ot` (list) + GET `/api/ot/{id}` (detail)
- PATCH `/api/ot/{id}/cancel`
  - 본인 PENDING만 취소 가능
  - ENDORSED 이후 취소 불가 → 409 `INVALID_STATUS`

3) `feat(api): POST /api/ot/{id}/endorse + /approve`
- `/endorse`
  - SUPERVISOR only
  - PENDING만 처리
  - self endorse 금지(403 `SELF_ENDORSE`)
  - 타 team OT 금지(403 `OT_WRONG_TEAM`)
  - `ot_approvals(stage=ENDORSE)` 생성
- `/approve`
  - ADMIN only
  - ENDORSED만 처리
  - self approve 금지(동일 코드 `SELF_ENDORSE` 사용)
  - PENDING 직접 결재 불가(409 `INVALID_STATUS`)
  - `ot_approvals(stage=APPROVE)` 생성

4) `feat(api): GET /api/ot/export/csv`
- date_from/date_to/status/user_id(ADMIN)/shop_id 필터
- `shop_id`는 Branch 06의 `user_shop_access` 기반 교차필터 사용
- CSV 컬럼:
  - `submitted_by_name`
  - `endorsed_by_name`, `endorsed_at`
  - `approved_by_name`, `approved_at`

5) `feat(ssr): /ot/new + /ot + /ot/{id} + /admin/ot-approve`
- `/ot/new`
  - HTMX 폼 제출
  - Technician Roster 검색
  - Supervisor bulk submit UI
- `/ot`
  - status/date/user/team 기준 필터 + pagination
- `/ot/{id}`
  - 상세 + 1차/2차 승인 이력 표시
- `/admin/ot-approve`
  - ENDORSED 건 최종 승인/반려 대기열

**DoD**
- OT submit/list/detail/cancel/endorse/approve/CSV export 동작
- 2단계 승인(stage=ENDORSE/APPROVE) 이력 정확
- 월 72h 한도 단건/벌크 시나리오 통과
- Admin OT Approve 화면에서 ENDORSED 건만 처리 가능

---

### Branch 05 — `feat/db-002-task-schema-distribution`
**목표**: Alembic 002로 Task Manager 스키마(shops/user_shop_access/task_items/task_snapshots)를 MiniPatch 12 기준으로 구축한다.

**커밋**
1) `feat(alembic): 002 create shops + user_shop_access + task tables (MSSQL, MiniPatch 12 baked-in)`
- shops, user_shop_access
- task_items
  - `work_package_id`
  - `assigned_supervisor_id`
  - `assigned_worker_id`
  - `distributed_at`
  - `planned_mh`
  - `is_active`, `deactivated_at`, `deactivated_by`
- task_snapshots
  - `supervisor_updated_at`
  - `version`, `is_deleted`, `deleted_at`, `deleted_by`
- 인덱스:
  - `idx_snap_meeting_deleted`
  - `idx_taskitem_shop`
  - `idx_taskitem_aircraft`
  - `idx_taskitem_active`
  - `idx_taskitem_wp`
  - `idx_task_items_supervisor`
  - `idx_task_items_worker`
  - `idx_task_items_distributed`
  - `idx_snap_task`

2) `feat(models): task/shop/access models`
- `app/models/task.py`, `shop.py`, `user_shop_access.py` 등
- relationship 정리(`work_package`, `assigned_supervisor`, `assigned_worker`)

3) `test(db): migration 002 smoke + indexes/assertions`
- 업그레이드 후 주요 index/FK/CHECK 확인

**DoD**
- migration 002 적용 성공
- distribution 관련 컬럼/인덱스 생성 확인
- `supervisor_updated_at` 포함
- 주의: `supervisor_updated_at`은 NEW/updated badge 추적용으로 **선택이 아니라 필수 반영**으로 구현

---

### Branch 06 — `feat/task-admin-shop-access`
**목표**: Shop & Shop access Admin 관리 + access service + OT export 교차필터를 구축한다.

**커밋**
1) `feat(service): shop_access_service (VIEW/EDIT/MANAGE) + ADMIN bypass`
- API/SSR 양쪽에서 재사용
- “권한 없음”은 403 `SHOP_ACCESS_DENIED`
- helper:
  - `check_shop_access(user, shop_id, required)`
  - `list_assignable_workers(shop_id, current_user)`

2) `feat(api): /api/shops CRUD (ADMIN)`
3) `feat(api): /api/shop-access CRUD (ADMIN)`
- GET/POST/DELETE

4) `chore(seed): shops + user_shop_access seed`
- 예: Sheet Metal / Fabric / Fiberglass / Painting
- ADMIN bypass와 supervisor sample access 포함

5) `feat(ot): /api/ot/export/csv shop_id cross filter`
- `shop_id` 제공 시: 해당 `shop_id`에 **user_shop_access가 존재하는 사용자**의 OT만 포함

6) `feat(ssr): /admin/shops + /admin/shop-access`
- Shop CRUD 화면
- user_shop_access 부여/회수 화면

**DoD**
- shop/access 관리 화면 또는 API로 권한 부여 가능
- task API 호출 시 shop access 검증 동작
- OT export shop_id 교차필터 동작
- ADMIN은 `user_shop_access` 행 없이도 모든 shop 접근 가능

---

### Branch 07 — `feat/task-core-snapshots-rfo`
**목표**: Task 핵심 API(조회/생성/수정)를 RFO/배포 필드까지 포함해 완성한다.

**커밋**
1) `feat(api): GET /api/tasks/snapshots (list)`
- required: `meeting_date`, `shop_id`
- optional filters:
  - `work_package_id`
  - `assigned_supervisor_id`
  - `aircraft_id`
  - `status`
  - `has_issue`
  - `include_deleted`
  - `airline_category: ALL|SQ|THIRD_PARTIES`
  - `page`, `per_page`
- response 필드:
  - `work_package_id`, `rfo_no`
  - `assigned_supervisor_id/name`
  - `assigned_worker_id/name`
  - `distributed_at`, `planned_mh`
  - `supervisor_updated_at`

2) `feat(api): POST /api/tasks (create task + snapshot)`
- EDIT+ on shop
- request support:
  - `work_package_id`
  - `assigned_supervisor_id`
  - `planned_mh`
- 규칙:
  - `assigned_supervisor_id` 제공 시 `distributed_at = NOW()` 자동 기록
  - Data Entry에서 supervisor가 add-task 할 때는 `assigned_supervisor_id = current_user.id` 자동 적용 가능

3) `feat(api): PATCH /api/tasks/snapshots/{id} (update with version)`
- version 필수
- 충돌: 409 `CONFLICT_VERSION` + `current_version`
- MH 감소 제한:
  - EDIT: 감소 불가(422 `MH_DECREASE_FORBIDDEN`)
  - MANAGE: 감소 허용, `correction_reason` 필수(422 `CORRECTION_REASON_REQUIRED`)
- supervisor가 Data Entry에서 수정한 경우 `supervisor_updated_at = NOW()` 갱신
- audit_logs 기록

4) `feat(query): task detail aggregate for SSR/partials`
- Task Detail 및 상세 오버레이에서 사용하는 read model
- snapshot history / distribution / audit summary 조회 쿼리 제공

**DoD**
- list/create/update 동작 + 권한/버전/MH 규칙 준수
- `airline_category`, `work_package_id`, `assigned_supervisor_id` 필터 동작
- create/update 응답에 distribution/RFO 필드 노출

---

### Branch 08 — `feat/task-lifecycle-batch`
**목표**: carry-over(init-week), batch update(all-or-nothing), soft delete/restore, deactivate/reactivate를 완성한다.

**커밋**
1) `feat(api): POST /api/tasks/init-week`
- MANAGE
- 중복 클릭 안전(idempotent)
- copy policy:
  - `mh_incurred_hours`, `status`, `remarks`, `critical_issue`, `has_issue`, `deadline_date`는 이전 주 그대로 복사
  - `correction_reason = NULL`
  - `supervisor_updated_at = NULL`
  - `version = 1`
- carry-over 제외:
  - `COMPLETED`
  - `is_deleted=true`
  - `task_items.is_active=false`

2) `feat(api): PATCH /api/tasks/snapshots/batch`
- EDIT+
- all-or-nothing: 하나라도 실패 시 전체 롤백
- 실패 응답: 422 `BATCH_VALIDATION_ERROR`(+errors) 또는 409

3) `feat(api): PATCH /api/tasks/snapshots/{id}/delete + /restore`
- MANAGE
- version +1, audit 기록

4) `feat(api): PATCH /api/tasks/{id}/deactivate + /reactivate`
- MANAGE
- `task_item.is_active` 토글, audit 기록

**DoD**
- init-week/batch/delete/restore/deactivate/reactivate 전체 동작
- carry-over copy/reset 규칙 준수
- batch는 트랜잭션 롤백 보장

---

### Branch 09 — `feat/task-distribution-ui`
**목표**: Task Manager / Data Entry / Settings + Task Distribution API + Task CSV export를 완성한다.

**커밋**
1) `feat(api): GET /api/tasks/export/csv`
- query: `meeting_date`, `shop_id`, `include_deleted`
- CSV 컬럼:
  - `work_package_id`, `rfo_no`
  - `assigned_supervisor_name`, `assigned_worker_name`
  - `distributed_at`, `planned_mh`
  - `weekly_mh_delta`
  - `updated_by_name`, `updated_at`

2) `feat(api): Task Distribution endpoints`
- POST `/api/tasks/import`
  - Excel/CSV 업로드 → parse → preview JSON 반환
- POST `/api/tasks/import/confirm`
  - preview 기준 DB 저장
  - all-or-nothing
  - `assigned_supervisor_id`, `distributed_at`, `planned_mh` 반영 가능
- POST `/api/tasks/{id}/assign`
- POST `/api/tasks/bulk-assign`
- PATCH `/api/tasks/{id}/assign-worker`
  - EDIT+
  - 같은 shop 내 worker만 허용
  - cross-shop worker 지정 시 403

3) `feat(ssr): /tasks (Task Manager)`
- 기존 `/tasks/meeting`를 **`/tasks`**로 변경
- ADMIN 주사용 / SUPERVISOR 읽기전용
- primary actions:
  - `Import RFO`
  - `Create & Assign`
  - `Assign To…`(bulk assign)
- secondary action:
  - `Init Week`(MANAGE 이상에서 유지, 헤더 overflow/menu 또는 보조 버튼)
- filters:
  - meeting_date, shop, airline, RFO, All Supervisors
- views:
  - Table / Kanban / RFO 3뷰 동기화
- pagination:
  - 10행/카드 기준
- detail overlay:
  - Distribution + Remarks + Active Issue + MH History + Audit Trail

4) `feat(ssr): /tasks/entry (Data Entry)`
- Supervisor 워크스테이션
- 본인 shop에 **배포된 task**만 표시
- status filter
- Quick Update 카드(상태/MH/issue)
- Worker Assignment 카드
- Add Task 모달
- NEW / NEEDS UPDATE / up-to-date badge
- 모바일 우선 반응형

5) `feat(ssr): /tasks/{task_id} + partial fragments`
- Task history / snapshot list / audit trail
- HTMX partials 분리(목록, 상세, badge, assign panel)

6) `feat(ssr): /admin/settings (System Settings)`
- Snapshot Week Config
  - `meeting_current_date`
  - auto-advance 관련 보조 UI
- Teams/Outlook/critical alert 토글 + recipients/template 입력(저장만, 실제 발송 X)
- 저장은 `/api/config PATCH` 호출 → audit_logs 자동 기록

**DoD**
- import preview/confirm/assign/assign-worker/export 동작
- `/tasks`, `/tasks/entry`, `/tasks/{task_id}`, `/admin/settings`에서 핵심 UX 동작 가능
- Task Manager는 ADMIN 주사용 / SUP 읽기전용이 지켜짐
- Data Entry는 배포된 task 중심으로 동작
- 에러 메시지/재시도 UX 준수

---

### Branch 10 — `feat/reporting-views-sql-expanded`
**목표**: Power BI용 reporting views를 MiniPatch 12까지 확장한다.

**커밋**
1) `feat(sql): scripts/create_views.py (SQL Server CREATE OR ALTER VIEW)`

Fact / core views:
- `vw_fact_ot_requests`
  - `submitted_by_name`
  - `endorser_name`, `endorsed_at`, `endorse_turnaround_hours`
  - `final_approver_name`, `approved_at`, `turnaround_hours`
  - `rfo_no`
- `vw_fact_task_snapshots`
  - `work_package_id`, `rfo_no`, `assigned_supervisor_name`, `assigned_worker_name`
  - `distributed_at`, `planned_mh`, `mh_variance`, `weekly_mh_delta`, `supervisor_updated_at`
- `vw_fact_task_snapshots_all`
  - 삭제 포함 전체 + `last_updated_by_name`

Dimension views:
- `vw_dim_employee`
- `vw_dim_aircraft`
- `vw_dim_work_package` (`rfo_no` 포함)
- `vw_dim_shop_stream`
- `vw_dim_shop`
- `vw_dim_task_status`
- `vw_dim_ot_reason`
- `vw_dim_date`

MiniPatch 12 추가 views:
- `vw_fact_ot_by_reason`
- `vw_fact_ot_weekly`
- `vw_rfo_efficiency`
- `vw_rfo_burndown`
- `vw_task_distribution`

2) `(optional) feat(alembic): 003 apply reporting views in prod`
- 운영에서 뷰 버전 관리가 필요하면 migration으로 승격

3) `test(sql): reporting smoke queries`
- 각 view에 대해 row fetch / schema contract 확인
- view 계산식은 SSOT §7.4 기준과 일치 여부 확인

**DoD**
- create_views 실행 시 뷰 생성/갱신 성공
- OT/Task/RFO 관련 최소 view contract 충족
- Power BI 연결에서 조회 가능

---

### Branch 11 — `feat/stats-rfo-dashboard`
**목표**: OT 통계 확장 + RFO summary/metrics API + OT/RFO 대시보드를 완성한다.

**커밋**
1) `feat(api): GET /api/stats/ot-summary + /ot-monthly-usage + /ot-by-reason + /ot-weekly-trend`
- `ot-summary`: date range + team filter
- `ot-monthly-usage`: 월 72h 대비 개인별 사용량
- `ot-by-reason`: reason code별 OT 시간 집계
- `ot-weekly-trend`: 주간별 OT 추세
- 권한:
  - SUPERVISOR+: 본인 team 범위
  - ADMIN: 전체 범위

2) `feat(api): GET /api/rfo/{work_package_id}/summary + /metrics + /blockers + /worker-allocation + /burndown`
- `summary`: task 상태 + OT 상태 통합 조회
- `metrics`: Productive Ratio / OT Ratio / FTC / Avg Cycle Time / MH Variance / blocker count / unassigned count
- `blockers`: active waiting+issue task 목록
- `worker-allocation`: worker별 MH/태스크 분포
- `burndown`: 주간별 MH 누적/잔여

3) `feat(ssr): /stats/ot`
- Individual Monthly OT vs 72h Limit
- OT by Reason
- Weekly OT Trend
- 팀 평균/경고 상태 표시

4) `feat(ssr): /rfo/{id} + /dashboard widget`
- 검색 가능한 RFO selector
- Summary Strip
- 6 KPI cards
- MH Burndown
- Efficiency Metrics
- Active Blockers
- Worker Allocation
- `/dashboard`에 RFO Progress 위젯 추가(기존 Recent Tasks 대체)

**DoD**
- OT stats API/SSR 동작 + 권한 필터 정상
- RFO summary/metrics/blockers/worker-allocation/burndown 응답 구조 검증 완료
- `/rfo/{id}` 화면에서 KPI/차트/목록이 연결됨

---

## 4. 의존성 / 실행 순서 체크

### 4.1 브랜치 실행 순서(권장)
1. Branch 00 — bootstrap
2. Branch 01 — core/OT/RFO schema
3. Branch 02 — auth/security
4. Branch 03 — admin/reference/config/import
5. Branch 04 — OT end-to-end
6. Branch 05 — task schema/distribution columns
7. Branch 06 — shop/shop-access/admin bypass
8. Branch 07 — task core snapshots/RFO filters
9. Branch 08 — task lifecycle/batch
10. Branch 09 — task distribution UI/API/settings
11. Branch 10 — reporting views
12. Branch 11 — stats/RFO dashboards

### 4.2 SSOT 마이그레이션 의존성 기준(요약)
1) users / roles / user_roles  
2) aircraft / work_packages(`rfo_no`) / shop_streams  
3) ot_requests / ot_approvals(`stage`)  
4) audit_logs  
5) system_config  
6) shops  
7) user_shop_access  
8) task_items(`work_package_id`, `assigned_*`, `distributed_at`, `planned_mh`)  
9) task_snapshots(`supervisor_updated_at`)  
10) future tables(DDL)  
11) reporting views  

> 위 순서를 기준으로 Branch 01(001), Branch 05(002), Branch 10(views)이 정합성을 갖는다.

### 4.3 추가 의존 메모
- Branch 04 OT CSV의 `shop_id` 교차필터는 Branch 06 의존
- Branch 09 Task Distribution API는 Branch 05 + 06 + 07 의존
- Branch 11 RFO/OT 통계 API는 Branch 10 view 의존을 권장

---

## 5. 최소 수용 테스트(요약)

### 5.1 OT (대표 13개)
- 본인 OT submit 성공(분 계산)
- `requested_minutes` 불일치 → 422 `VALIDATION_ERROR`
- 중복 시간 겹침 → 422 `DUPLICATE_OT`
- supervisor bulk submit(같은 team) → `created_count` / `skipped_count` 확인
- cross-team bulk → 403
- 월 72h 초과(단건) → 422 `OT_MONTHLY_LIMIT_EXCEEDED`
- 월 72h 초과(벌크) → 해당 user skip(`MONTHLY_LIMIT_EXCEEDED`)
- supervisor endorse approve/reject → `ENDORSED` / `REJECTED` + stage row
- admin approve/reject endorsed → `APPROVED` / `REJECTED` + stage row
- admin tries approve pending → 409 `INVALID_STATUS`
- worker cancels pending → `CANCELLED`
- worker tries cancel endorsed → 409 `INVALID_STATUS`
- OT CSV export 200 + 헤더(Content-Type/Disposition) + `endorsed_*` / `approved_*` 컬럼 확인

### 5.2 Task / Distribution (대표 16개)
- init-week 최초 → carry-over 생성(`created_count > 0`)
- init-week 재호출 → idempotent(`created_count=0`, `skipped_count > 0`)
- `COMPLETED` / `is_deleted=true` / `is_active=false` task는 carry-over 제외
- carry-over 시 `mh_incurred_hours` 이전 주 값 복사 확인
- carry-over 시 `supervisor_updated_at = NULL` 초기화 확인
- snapshots list with `airline_category` / `work_package_id` / `assigned_supervisor_id` 필터
- create task with `work_package_id`, `assigned_supervisor_id`, `planned_mh` → `distributed_at` 자동 설정
- update snapshot version 충돌 409
- MH 감소 제한(EDIT 422 / MANAGE + correction_reason 필수)
- batch update all-or-nothing
- delete/restore version +1
- task deactivate/reactivate
- Task Import Preview → `valid_count`/`error_count` 정확성
- Task Import Confirm → all-or-nothing + audit
- Assign / Bulk Assign → `assigned_supervisor_id`, `distributed_at` 설정
- Assign Worker → 본인 shop 내 worker만 허용, cross-shop 403

### 5.3 Analytics / Reporting (대표 10개)
- `/api/stats/ot-summary` role scope 검증
- `/api/stats/ot-monthly-usage` month filter/기본값 검증
- `/api/stats/ot-by-reason` 집계 검증
- `/api/stats/ot-weekly-trend` label/hours 집계 검증
- `/api/rfo/{id}/summary` task+ot 통합 응답 검증
- `/api/rfo/{id}/metrics` KPI 계산 필드 검증
- `/api/rfo/{id}/blockers` / `/worker-allocation` / `/burndown` 응답 스키마 검증
- `vw_fact_task_snapshots(_all)`에 `rfo_no`, `assigned_*`, `planned_mh`, `supervisor_updated_at` 존재
- `vw_fact_ot_requests`에 1차/2차 승인 컬럼 존재
- `vw_task_distribution` / `vw_rfo_efficiency` / `vw_rfo_burndown` 조회 가능

### 5.4 Security / Concurrency (대표 10개)
- 로그인 전 `/api` 접근 401
- CSRF 누락 403
- rate limit 429
- optimistic locking 동시 업데이트 테스트
- batch: 1건 version 충돌 → 전체 롤백
- batch: 1건 field 오류 → 전체 롤백
- 타 shop 접근(access 없음) → 403 `SHOP_ACCESS_DENIED`
- ADMIN + `user_shop_access` 행 없음 → 전체 shop 접근 가능
- VIEW 사용자 → snapshot 수정 시도 → 403
- EDIT 사용자 → soft delete / init-week 시도 → 403

### 5.5 권장 테스트 파일 매핑
- `tests/test_auth_security.py`
- `tests/test_users.py`
- `tests/test_reference_import.py`
- `tests/test_ot.py`
- `tests/test_task_core.py`
- `tests/test_task_lifecycle.py`
- `tests/test_task_distribution.py`
- `tests/test_stats_rfo.py`
- `tests/test_reporting_views.py`

---

## 6. 리스크 / 주의사항(업데이트)

- **MSSQL Async 드라이버/ODBC18**: 로컬/CI에서 드라이버 설치 이슈 가능 → Docker 기반 실행을 1순위로 고정
- **Azure AD Dev 세팅**: 로컬에서도 App Registration 필요 → 테스트에서는 아래 방식으로 우회:
  - **추천: Dependency Override**
    ```python
    app.dependency_overrides[get_current_user] = lambda: test_worker_user
    ```
    테스트 종료 시 `app.dependency_overrides.clear()` 필수
  - **대안: 세션 직접 주입** — 세션 쿠키 + CSRF 고정값을 httpx client에 설정
  - CSRF 미들웨어는 테스트 환경에서 `ENV=test` 조건으로 disable 가능
- **Migration rebase 금지**: 이미 운영/공유된 001/002 migration을 덮어쓰지 않는다. 기존 배포본이 있으면 additive revision으로 분리
- **월 OT 한도 계산**: timezone/월경계/상태 집계(APPROVED + PENDING + ENDORSED, CANCELLED 제외)를 서비스 레이어에서 일관되게 구현
- **`team` vs `shop` 분리 주의**:
  - OT 스코프는 `team`
  - Task 스코프는 `user_shop_access` + `shop`
  - 용어를 혼용하지 않도록 serializer/filter 이름을 분리
- **Worker Assignment 1:1 한계**: MVP는 `assigned_worker_id` 단일 FK. 1:N 요구가 생기면 API/DDL/UI 모두 바뀜(MiniPatch 13+)
- **Import Preview/Confirm 메모리 사용량**: 업로드 파싱은 스트리밍 또는 제한된 메모리 사용으로 구현. preview payload가 커질 경우 row cap/페이징 고려
- **`supervisor_updated_at` 의미**: Data Entry에서 supervisor가 실제 수정한 경우에만 갱신해야 NEW/updated badge가 일관됨
- **Init-week UX 변경 주의**: Task Manager primary CTA는 Import/Create & Assign로 바뀌었지만, init-week 기능은 여전히 MVP에 포함되므로 UI에서 숨기지 말 것
- **System Settings key 화이트리스트**: unknown key 자동 생성 금지, PATCH는 batch atomic 처리 권장

---

## 7. 변경 이력(이번 문서 수정 요약)

- MiniPatch 11 반영:
  - OT 2단계 승인(`endorse`/`approve`) 반영
  - 월 72h 한도(`OT_MONTHLY_LIMIT_EXCEEDED`) 반영
  - `work_packages.rfo_no` + RFO summary API 반영
  - Reference CSV import 반영
  - Task의 `work_package_id` 연계 및 OT/Task CSV 컬럼 확장 반영
- MiniPatch 12 반영:
  - `task_items.assigned_supervisor_id`, `assigned_worker_id`, `distributed_at`, `planned_mh`
  - `task_snapshots.supervisor_updated_at`
  - Task Distribution API(`import`, `confirm`, `assign`, `bulk-assign`, `assign-worker`)
  - `/tasks` Task Manager, `/tasks/entry` Data Entry 역할 재정의
  - `/rfo/{id}` RFO Detail + `/stats/ot` 확장
  - 추가 reporting views 5종 반영
- 기존 MiniPatch 8~10 유지:
  - MSSQL/Azure SQL + ODBC18
  - Azure AD OAuth2 + session + CSRF + rate limit
  - `system_config` + `/api/config` + `/admin/settings`
  - `airline_category` 필터, `INVALID_STATUS`, reporting base views, audit 정합 유지

---

## 8. 정합 감사 메모(작성자 체크)

다음 항목이 본 문서에 반영되어 있어야 한다.

- [x] 문서 제목 / 기준 SSOT가 **MiniPatch 1~12**로 갱신됨
- [x] OT 2단계 승인 / 월 72h 한도 / `rfo_no` / Reference CSV import 반영
- [x] Task Distribution 스키마(`assigned_*`, `distributed_at`, `planned_mh`, `supervisor_updated_at`) 반영
- [x] `/api/tasks/import`, `/api/tasks/import/confirm`, `/api/tasks/{id}/assign`, `/api/tasks/bulk-assign`, `/api/tasks/{id}/assign-worker` 반영
- [x] `/tasks` Task Manager / `/tasks/entry` Data Entry / `/rfo/{id}` RFO Detail 반영
- [x] `/api/stats/ot-monthly-usage`, `/api/stats/ot-by-reason`, `/api/stats/ot-weekly-trend`, `/api/rfo/{id}/metrics` 반영
- [x] reporting views 5종(`vw_fact_ot_by_reason`, `vw_fact_ot_weekly`, `vw_rfo_efficiency`, `vw_rfo_burndown`, `vw_task_distribution`) 반영
- [x] 기존 MiniPatch 8~10 핵심 규칙(auth, config, airline, audit, MSSQL) 유지

