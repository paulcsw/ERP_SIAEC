# ERP 통합 SSOT v2.0 (MiniPatch 1~12 Applied)
**MH Tracking (OT) + Task Manager (Weekly Snapshot) 통합 단일 기준 문서**

- 문서 버전: v2.0 (Audit-Patched, MiniPatch 1~12 Applied)
- 기준일: 2026-03-09 (Asia/Singapore)
- 기반 문서:
  - MH Tracking Integrated System — MVP SSOT v1.0 (2026-02-13)
  - Task Manager 프로젝트 SSOT v1.0 (2026-02-13)
  - ERP 통합 SSOT v1.0 (2026-02-26)
  - 논리감사 보고서 v2 (2026-02-26)
- 변경사항 요약:
  - 감사 Critical 9건, Medium 14건, Minor 5건 전체 반영
  - Open Decision #1, #2 해소 (mh 누적 합산, carry-over 전 필드 복사)
  - Base A(SSOT_MVP) 상세 복원 (DDL, 인덱스, 에러코드, 프로젝트 구조 등)
  - 보안 강화 (CSRF, 인증 강제, rate limiting)
  - 동시성 제어 (optimistic locking)
  - 배치 API + all-or-nothing 트랜잭션
  - correction_reason 필드 (MH 감소 감사 추적)
  - CSS 프레임워크 확정 (Tailwind CDN)
  - MiniPatch 1~12 반영 (MiniPatch 1~9 + MiniPatch 10: 감사 결함 7건 수정 + MiniPatch 11: RFO No.(work_packages.rfo_no) + CSV Import(Reference Data) + 월 72h OT 한도(제출 차단/위젯) + 2단계 OT 승인(PENDING→ENDORSED→APPROVED) + Task-RFO 연결(task_items.work_package_id) + MiniPatch 12: Task Distribution/Worker Assignment/Planned MH + Task Manager·Data Entry 역할 분리 + RFO Detail Lean Metrics + OT by Reason/Weekly Trend + 모바일 최적화)

---

## 0. 문서 목적과 적용 원칙

### 0.1 목적
본 SSOT는 ERP 프로젝트에서 **OT(연장근무)** 기능과 **Task Manager(주간 회의 스냅샷 기반 작업관리)** 기능을 **하나의 시스템/DB/권한/감사 기준**으로 구현·운영하기 위한 "Single Source of Truth"이다.

**이 문서만으로 개발자가 구현할 수 있어야 한다.** Base SSOT(SSOT_MVP, Task_Manager_SSOT)를 별도 참조할 필요가 없도록 모든 상세를 포함한다.

### 0.2 원칙
1) **System of Record는 ERP DB(Microsoft SQL Server)** 이다.
2) UI(웹)는 ERP 방식(서버 렌더 + HTMX)을 기본으로 한다.
3) 모든 "쓰기(Create/Update/Delete/Restore/Approve/Deactivate 등)"는 **audit_logs**에 기록한다.
4) 운영에서 사용자는 DB 권한을 갖지 않는다. **권한은 앱(RBAC)으로 통제**한다.
5) **사용자 물리 삭제는 원칙적으로 금지한다.**
   기본은 비활성화(`is_active=false`)만 허용한다.  
   단, 아래 조건을 모두 만족하는 경우 ADMIN이 물리 삭제(HARD DELETE)할 수 있다:
   - `audit_logs.actor_id`로 참조된 행이 0건
   - `ot_requests.user_id`로 참조된 행이 0건
   - `ot_requests.submitted_by`로 참조된 행이 0건
   - `ot_approvals.approver_id`로 참조된 행이 0건
   - `task_snapshots.last_updated_by` 또는 `task_snapshots.deleted_by`로 참조된 행이 0건
   위 조건을 하나라도 충족하지 못하면 물리 삭제 불가 → **422 USER_HAS_REFERENCES** 반환.
   이 경우 비활성화만 허용한다.

6) Power BI 리포팅은 **SQL Server View(Star Schema) 기반**으로 제공한다.

7) Task Manager의 Airline 필터(SQ / Third Parties) 분류 규칙은 §7.2.8을 따른다.

---

## 1. MVP 범위(Scope)

### 1.1 In Scope
- Phase 0 Foundations (RBAC, audit_logs 포함)
- Phase 1 OT Request / 2단계 Endorsement(SUP→ADMIN) / 월 72h 한도 / Stats
- Phase 1-B Task Manager / Data Entry (주간 스냅샷 기반 Task 배포·감사·현장 업데이트: Task Manager, Data Entry, carry-over, deadline clear, soft delete, Power BI view)
- RFO No. 연결 (work_packages + task_items) + CSV Import (Reference Data)
- CSV Export API (OT/Task) (MVP 포함)

### 1.2 Out of Scope (본 문서에서 스키마만 선반영 또는 제외)
- Attendance(shift_templates/shift_assignments/attendance_events) 기능 구현
- MH ledger calculation(time_ledger_daily/ledger_allocations_daily) 구현
- Daily assignments, worklog blocks 구현
- Overcost analytics
- Task 추천/최적화 엔진
- Celery/Redis 기반 비동기 잡(Phase 2+)
- 알림/스케줄러(Phase 2+, 현재 Power Automate 유지 가능)
- Microsoft Teams webhook 연동 (회의 요약 자동 전송) (Phase 2)
- Outlook email 자동 발송 (OT 승인 알림, 리마인더) (Phase 2)
  - Settings UI에는 토글 표시하되, 실제 연동은 Phase 2에서 구현
  - Phase 2 착수 시 별도 스펙 문서 작성 필요

---

## 2. 기술 스택(ERP 방식 고정)

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | ERP 런타임 |
| Web | FastAPI | REST API + SSR endpoints |
| UI | Jinja2 + HTMX | 서버 렌더링 + 최소 JS |
| CSS | Tailwind CSS (CDN) | 경량 스타일링 |
| DB | Microsoft SQL Server (사내 인스턴스 또는 Azure SQL) | System of Record |
| Driver | ODBC Driver 18 + aioodbc (async) / pyodbc (sync) | SQLAlchemy dialect: `mssql+aioodbc` |
| ORM | SQLAlchemy 2.x | Async (권장) |
| Migration | Alembic | 스키마 버전 |
| Validation | Pydantic v2 | request/response |
| Auth | Azure AD OAuth2 (Authorization Code Flow) | 서버사이드 세션(기본 8h) |
| CSRF | Double Submit Cookie | 모든 POST/PATCH/DELETE 적용 |
| Rate Limit | slowapi (또는 동등) | 분당 120 req/user |
| Reporting | SQL Server Views → Power BI | fact/dim |
| Testing | pytest + httpx | unit + integration |

---

## 3. 용어 정의(Glossary)

| 용어 | 정의 |
|---|---|
| **ACReg** | 항공기 등록번호 (aircraft.ac_reg) |
| **Work Package(WP)** | 정비 패키지 (work_packages). OT 태깅용 |
| **Shop Stream** | WP 하위 공정 (shop_streams). OT 태깅용. **Task Manager의 Shop과 별도 엔티티** |
| **Shop** | Task Manager의 작업 워크숍 (shops 테이블). 예: Sheet Metal, Fiberglass. shop_streams와는 현재 별도이며, 통합은 Phase 2에서 결정 |
| **Meeting Date** | 주간 회의 기준일(스냅샷 기준일). 기본 로직에서 "저번 주" = meeting_date − 7일 |
| **Task Item** | "작업 정의(ACReg + Shop + Task text)"의 안정적인 엔티티 (task_items) |
| **Task Snapshot** | 특정 meeting_date에 대한 Task의 주간 상태/맨아워/이슈 스냅샷 (task_snapshots) |
| **mh_incurred_hours** | **누적 합산** 맨아워. 해당 task에 meeting_date 시점까지의 총 투입. 주간 증분이 아님 |
| **RFO (Refurbishment Order)** | SAP 정비 작업 주문서. SSOT의 work_packages 테이블에 매핑. rfo_no 필드로 식별 |
| **ENDORSED** | OT 2단계 승인 중간 상태. SUPERVISOR 1차 승인 완료, ADMIN 최종 결재 대기 |

---

## 4. 프로젝트 구조

```
mh-tracking/
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       ├── 001_initial_schema.py          # Base tables + OT
│       └── 002_task_manager.py            # Task Manager tables + enums
├── app/
│   ├── __init__.py
│   ├── main.py                            # FastAPI app factory
│   ├── config.py                          # Settings (Pydantic BaseSettings)
│   ├── database.py                        # SQLAlchemy engine + session
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── csrf.py                        # CSRF Double Submit Cookie
│   │   └── rate_limit.py                  # Rate limiting (slowapi)
│   ├── models/                            # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── user.py                        # users, roles, user_roles
│   │   ├── reference.py                   # aircraft, work_packages, shop_streams
│   │   ├── ot.py                          # ot_requests, ot_approvals
│   │   ├── task.py                        # shops, user_shop_access, task_items, task_snapshots
│   │   ├── attendance.py                  # (schema only — Phase 2)
│   │   ├── tracking.py                    # (schema only — Phase 3)
│   │   ├── ledger.py                      # (schema only — Phase 3)
│   │   ├── audit.py                       # audit_logs
│   │   └── system_config.py               # system_config (Admin Settings)
│   ├── schemas/                           # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── ot.py
│   │   ├── task.py                        # Task 관련 request/response
│   │   ├── common.py                      # pagination wrapper, error format
│   │   └── system_config.py               # /api/config request/response
│   ├── api/                               # FastAPI routers (JSON API)
│   │   ├── __init__.py
│   │   ├── deps.py                        # Dependency injection (auth, db, current_user, shop_access)
│   │   ├── auth.py                        # Login / logout / session
│   │   ├── ot.py                          # OT request + endorsement endpoints
│   │   ├── tasks.py                       # Task init-week, CRUD, batch, deactivate
│   │   ├── admin.py                       # Shops CRUD, user_shop_access CRUD
│   │   ├── users.py                       # User management (admin)
│   │   ├── reference.py                   # Aircraft, WP, shop_stream CRUD
│   │   └── config.py                      # system_config endpoints (/api/config)
│   ├── views/                             # HTMX server-rendered views
│   │   ├── __init__.py
│   │   ├── dashboard.py                   # Role-based dashboards
│   │   ├── ot.py                          # OT form, list, detail views
│   │   ├── tasks.py                       # Meeting console, data entry views
│   │   ├── stats.py                       # OT statistics page
│   │   └── settings.py                    # Admin Settings (system_config)
│   ├── services/                          # Business logic layer
│   │   ├── __init__.py
│   │   ├── ot_service.py                  # OT submission, endorsement logic
│   │   ├── task_service.py                # Init-week, snapshot CRUD, batch, MH validation
│   │   ├── shop_access_service.py         # Shop access check (ADMIN bypass 포함)
│   │   ├── audit_service.py               # Audit log writer
│   │   ├── stats_service.py               # OT + Task statistics queries
│   │   └── config_service.py              # system_config CRUD
│   ├── templates/                         # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── components/                    # Reusable HTMX partials
│   │   │   ├── nav.html                   # Role-aware navigation
│   │   │   ├── status_badge.html          # Color-coded status badge
│   │   │   ├── endorse_buttons.html       # OT approve/reject
│   │   │   └── pagination.html            # Pagination controls
│   │   ├── dashboard/
│   │   ├── ot/
│   │   │   ├── form.html                  # OT submit form
│   │   │   ├── table.html                 # OT list (paginated)
│   │   │   └── detail.html                # OT detail + approval history
│   │   ├── tasks/
│   │   │   ├── meeting.html               # 회의 콘솔 (init-week + 인라인 편집)
│   │   │   ├── entry.html                 # 현장 입력 (단건 상태/MH/remarks)
│   │   │   └── detail.html                # Task 상세 (히스토리/스냅샷)
│   │   ├── admin/
│   │   │   ├── shops.html
│   │   │   ├── shop_access.html
│   │   │   └── settings.html               # Admin Settings (system_config)
│   │   └── stats/
│   └── static/                            # CSS, minimal JS
├── scripts/
│   ├── seed_data.py                       # Dev seed: users, roles, aircraft, WPs, shops
│   └── create_views.py                    # Power BI reporting views (SQL)
├── tests/
│   ├── conftest.py                        # Fixtures: db_session, worker/supervisor/admin clients
│   ├── test_ot.py
│   ├── test_tasks.py                      # Task 핵심 시나리오
│   ├── test_shop_access.py                # Shop access + ADMIN bypass
│   ├── test_concurrency.py                # Optimistic locking
│   └── test_audit.py
├── requirements.txt
├── docker-compose.yml                     # App container (DB: external MSSQL)
├── Dockerfile
└── README.md
```

---

## 5. 데이터 모델(DB SSOT)

### 5.1 Enum 정의 (MSSQL 구현)

SQL Server에는 PostgreSQL의 `CREATE TYPE ... AS ENUM`이 없으므로, 본 SSOT는 **NVARCHAR 컬럼 + CHECK 제약**으로 enum을 구현한다. (값은 아래 목록으로 고정)

- role_name: `WORKER`, `SUPERVISOR`, `ADMIN`
- ot_status: `PENDING`, `ENDORSED`, `APPROVED`, `REJECTED`, `CANCELLED`
- ot_approval_action: `APPROVE`, `REJECT`
- ot_approval_stage: `ENDORSE`, `APPROVE`  (1차=SUP, 2차=ADMIN)
- ot_reason_code: `BACKLOG`, `AOG`, `SCHEDULE_PRESSURE`, `MANPOWER_SHORTAGE`, `OTHER`
- work_status: `ACTIVE`, `COMPLETED`, `ON_HOLD`, `CANCELLED`
- task_status: `NOT_STARTED`, `IN_PROGRESS`, `WAITING`, `COMPLETED`
- shop_access_role: `VIEW`, `EDIT`, `MANAGE`

**task_status DB Value ↔ Display Name 매핑(변경 없음):**

| DB Value | Display Name | UI Color |
|---|---|---|
| NOT_STARTED | Not started | Grey (#9CA3AF) |
| IN_PROGRESS | In progress | Yellow (#EAB308) |
| WAITING | Waiting | Red (#EF4444) |
| COMPLETED | Completed | Green (#22C55E) |

API 입력: DB 값을 기본으로 사용. Display Name으로 입력해도 서버에서 normalize 수용.

### 5.2 Core Tables (DDL) — Microsoft SQL Server

> 표기 규칙: timezone 포함 timestamp → `DATETIMEOFFSET`, 논리값(true/false) → `BIT(0/1)`, JSON 컬럼 → `NVARCHAR(MAX)`(JSON 문자열)

#### users (Azure AD 매핑 포함)

- MiniPatch 9 반영:
  - 기존 자체 로그인용 password hash 컬럼 **삭제**
  - `azure_oid` 컬럼 **추가** (Azure AD id_token의 `oid`와 매칭)
  - `azure_oid`는 NULL 허용 + NULL이 아닌 경우에만 unique (filtered unique index)

```sql
CREATE TABLE users (
    id           BIGINT IDENTITY(1,1) PRIMARY KEY,
    employee_no  NVARCHAR(20)  NOT NULL,
    name         NVARCHAR(100) NOT NULL,
    email        NVARCHAR(255) NULL,
    team         NVARCHAR(50)  NULL,
    is_active    BIT           NOT NULL DEFAULT 1,              -- 물리 삭제 금지, 비활성화만(예외적 hard delete 조건은 §0.2)
    azure_oid    NVARCHAR(128) NULL,                             -- Entra ID(OIDC) object id
    created_at   DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    updated_at   DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT uq_users_employee_no UNIQUE (employee_no)
);

-- email은 NULL 다중 허용을 위해 filtered unique index 사용
CREATE UNIQUE INDEX uq_users_email_not_null ON users(email) WHERE email IS NOT NULL;

-- azure_oid도 NULL 다중 허용
CREATE UNIQUE INDEX uq_users_azure_oid_not_null ON users(azure_oid) WHERE azure_oid IS NOT NULL;

CREATE INDEX idx_users_team ON users(team);
```

#### roles & user_roles

```sql
CREATE TABLE roles (
    id   INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(20) NOT NULL UNIQUE
         CHECK (name IN ('WORKER', 'SUPERVISOR', 'ADMIN'))
);
-- Seed: INSERT INTO roles (name) VALUES ('WORKER'), ('SUPERVISOR'), ('ADMIN');

CREATE TABLE user_roles (
    user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    role_id  INT    NOT NULL REFERENCES roles(id) ON DELETE NO ACTION,
    CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id)
);
```

#### aircraft / work_packages / shop_streams

```sql
CREATE TABLE aircraft (
    id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    ac_reg     NVARCHAR(20) NOT NULL UNIQUE,
    airline    NVARCHAR(100) NULL,
    status     NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
              CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
    created_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

CREATE TABLE work_packages (
    id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    aircraft_id BIGINT NOT NULL REFERENCES aircraft(id) ON DELETE NO ACTION,
    rfo_no      NVARCHAR(50) NULL,                          -- SAP RFO No. (수동 입력 또는 CSV import)
    title       NVARCHAR(200) NOT NULL,
    start_date  DATE NULL,
    end_date    DATE NULL,
    priority    SMALLINT NULL DEFAULT 0,
    status      NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
               CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
    created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);
CREATE UNIQUE INDEX uq_wp_rfo_no_not_null ON work_packages(rfo_no) WHERE rfo_no IS NOT NULL;
CREATE INDEX idx_wp_aircraft ON work_packages(aircraft_id);
CREATE INDEX idx_wp_status ON work_packages(status);

CREATE TABLE shop_streams (
    id              BIGINT IDENTITY(1,1) PRIMARY KEY,
    work_package_id BIGINT NOT NULL REFERENCES work_packages(id) ON DELETE NO ACTION,
    shop_code       NVARCHAR(20) NOT NULL,
    status          NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
    created_at      DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT uq_shop_stream UNIQUE (work_package_id, shop_code)
);
CREATE INDEX idx_ss_wp ON shop_streams(work_package_id);
```

#### ot_requests

```sql
CREATE TABLE ot_requests (
    id                BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    submitted_by      BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,     -- NULL = 본인 신청, NOT NULL = 대리 제출자(SUPERVISOR)
    work_package_id   BIGINT NULL REFERENCES work_packages(id) ON DELETE NO ACTION, -- optional tag
    shop_stream_id    BIGINT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,  -- optional tag
    [date]            DATE NOT NULL,
    start_time        TIME NOT NULL,
    end_time          TIME NOT NULL,
    requested_minutes INT NOT NULL CHECK (requested_minutes > 0),
    reason_code       NVARCHAR(30) NOT NULL DEFAULT 'OTHER'
                     CHECK (reason_code IN ('BACKLOG','AOG','SCHEDULE_PRESSURE','MANPOWER_SHORTAGE','OTHER')),
    reason_text       NVARCHAR(MAX) NULL,
    status            NVARCHAR(20) NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','ENDORSED','APPROVED','REJECTED','CANCELLED')),
    created_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    updated_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT chk_ot_time CHECK (end_time > start_time)
);

CREATE INDEX idx_ot_user_date ON ot_requests(user_id, [date]);
CREATE INDEX idx_ot_status ON ot_requests(status);
CREATE INDEX idx_ot_date ON ot_requests([date]);
CREATE INDEX idx_ot_submitted_by ON ot_requests(submitted_by) WHERE submitted_by IS NOT NULL;
```

#### ot_approvals

```sql
CREATE TABLE ot_approvals (
    id              BIGINT IDENTITY(1,1) PRIMARY KEY,
    ot_request_id   BIGINT NOT NULL REFERENCES ot_requests(id) ON DELETE NO ACTION,
    approver_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    stage           NVARCHAR(20) NOT NULL
                   CHECK (stage IN ('ENDORSE','APPROVE')),   -- ENDORSE=1차(SUP), APPROVE=2차(ADMIN)
    action          NVARCHAR(20) NOT NULL
                   CHECK (action IN ('APPROVE','REJECT')),
    comment         NVARCHAR(MAX) NULL,
    acted_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

CREATE INDEX idx_ota_request ON ot_approvals(ot_request_id);
CREATE INDEX idx_ota_approver ON ot_approvals(approver_id);
CREATE INDEX idx_ota_stage ON ot_approvals(stage);
```

#### audit_logs

MSSQL에는 Postgres의 JSON 전용 타입이 없으므로 `before_json`, `after_json`는 **JSON 문자열(NVARCHAR(MAX))** 로 저장한다.  
애플리케이션 레벨에서 `json.dumps(..., ensure_ascii=False)` / `json.loads(...)`로 직렬화/역직렬화한다.

```sql
CREATE TABLE audit_logs (
    id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    actor_id    BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
    entity_type NVARCHAR(50) NOT NULL,        -- e.g., 'ot_request', 'task_snapshot', 'shop', 'system_config'
    entity_id   BIGINT NOT NULL,
    action      NVARCHAR(20) NOT NULL,        -- 'CREATE', 'UPDATE', 'DELETE', 'RESTORE', 'DEACTIVATE', 'REACTIVATE'
    before_json NVARCHAR(MAX) NULL,           -- null for CREATE
    after_json  NVARCHAR(MAX) NULL,           -- null for DELETE
    created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_actor ON audit_logs(actor_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at);
```

#### system_config

Admin Settings(회의 기준일/자동 advance, Teams/Outlook 토글·수신자·템플릿 등) 저장용 테이블.

```sql
CREATE TABLE system_config (
    id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    [key]      NVARCHAR(100) NOT NULL,
    value      NVARCHAR(MAX) NOT NULL DEFAULT '',
    updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT uq_system_config_key UNIQUE ([key])
);


-- Seed data (MVP defaults)
INSERT INTO system_config ([key], value) VALUES
  ('meeting_current_date',       '2026-02-26'),
  ('meeting_auto_advance',       'every_monday'),
  ('teams_enabled',              'true'),
  ('teams_recipients',           '#cis-sheet-metal'),
  ('teams_message_template',     'Weekly Summary — {shop} · Week {week}: {task_count} tasks, {issues} issues flagged.'),
  ('outlook_enabled',            'false'),
  ('outlook_recipients',         ''),
  ('outlook_subject_template',   '[CIS ERP] OT Approval Reminder — {date}'),
  ('outlook_body_template',      'You have {pending_count} pending OT requests awaiting approval for {shop}. Please review at your earliest convenience.'),
  ('critical_alert_enabled',     'true'),
  ('critical_alert_recipients',  '#cis-alerts');
```

### 5.3 Task Manager 테이블

#### 5.3.1 shops

```sql
CREATE TABLE shops (
    id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    code       NVARCHAR(50)  NOT NULL UNIQUE,     -- 내부 식별자 (예: SHEET_METAL)
    name       NVARCHAR(200) NOT NULL,            -- 표시명 (예: Sheet Metal)
    created_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    updated_at DATETIMEOFFSET NULL,
    created_by BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION
);
```

#### 5.3.2 user_shop_access

```sql
CREATE TABLE user_shop_access (
    id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    shop_id    BIGINT NOT NULL REFERENCES shops(id) ON DELETE NO ACTION,
    access     NVARCHAR(20) NOT NULL
              CHECK (access IN ('VIEW','EDIT','MANAGE')),
    granted_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    granted_by BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    CONSTRAINT uq_user_shop UNIQUE (user_id, shop_id)
);
```

#### 5.3.3 task_items

```sql
CREATE TABLE task_items (
    id                     BIGINT IDENTITY(1,1) PRIMARY KEY,
    aircraft_id            BIGINT NOT NULL REFERENCES aircraft(id) ON DELETE NO ACTION,
    shop_id                BIGINT NOT NULL REFERENCES shops(id) ON DELETE NO ACTION,
    work_package_id        BIGINT NULL REFERENCES work_packages(id) ON DELETE NO ACTION,  -- RFO 연결 (NULL 허용)
    assigned_supervisor_id BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,          -- Admin이 배포한 Supervisor
    assigned_worker_id     BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,          -- Supervisor가 배정한 Worker (MVP 1:1)
    distributed_at         DATETIMEOFFSET NULL,                                           -- Admin 배포 시점
    planned_mh             DECIMAL(8,2) NULL,                                             -- 초기 계획 MH (Variance = actual - planned)
    task_text              NVARCHAR(MAX) NOT NULL,
    is_active              BIT NOT NULL DEFAULT 1,                                        -- 비활성화 시 0 (carry-over 제외)
    deactivated_at         DATETIMEOFFSET NULL,
    deactivated_by         BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
    created_at             DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    created_by             BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION
);

CREATE INDEX idx_taskitem_shop ON task_items(shop_id);
CREATE INDEX idx_taskitem_aircraft ON task_items(aircraft_id);
CREATE INDEX idx_taskitem_active ON task_items(is_active);
CREATE INDEX idx_taskitem_wp ON task_items(work_package_id) WHERE work_package_id IS NOT NULL;
CREATE INDEX idx_task_items_supervisor ON task_items(assigned_supervisor_id) WHERE assigned_supervisor_id IS NOT NULL;
CREATE INDEX idx_task_items_worker ON task_items(assigned_worker_id) WHERE assigned_worker_id IS NOT NULL;
CREATE INDEX idx_task_items_distributed ON task_items(distributed_at) WHERE distributed_at IS NOT NULL;
```

**설계 결정 (MiniPatch 12 반영):**
- Worker 배정은 **1:1 FK**로 고정한다. UI에 `+ Add` 버튼이 있어도 MVP에서는 단일 Worker만 지원한다.
- 향후 1:N 또는 M:N 배정이 필요해지면 `task_worker_assignments` 조인 테이블로 전환한다. (MiniPatch 13+)
- `planned_mh`는 스냅샷이 아니라 **task_items**에 둔다. (초기 계획값이므로 meeting_date별로 변하지 않음)
- `distributed_at`은 ADMIN이 Assign/Bulk Assign/Import Confirm 시 서버가 자동 기록한다.

#### 5.3.4 task_snapshots (핵심 Fact)

```sql
CREATE TABLE task_snapshots (
    id                    BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id               BIGINT NOT NULL REFERENCES task_items(id) ON DELETE CASCADE,
    meeting_date          DATE NOT NULL,
    status                NVARCHAR(20) NOT NULL DEFAULT 'NOT_STARTED'
                         CHECK (status IN ('NOT_STARTED','IN_PROGRESS','WAITING','COMPLETED')),
    mh_incurred_hours     NUMERIC(10,2) NOT NULL DEFAULT 0,
                          -- **누적 합산**. meeting_date 시점까지의 총 투입 맨아워.
                          -- 예: 1주차 1.0 → 2주차 3.0 → 3주차 7.0
                          -- 주간 투입량은 리포트에서 (이번 주 - 저번 주)로 계산
    remarks               NVARCHAR(MAX) NULL,
    critical_issue        NVARCHAR(MAX) NULL,
    has_issue             BIT NOT NULL DEFAULT 0,
    deadline_date         DATE NULL,                        -- NULL 허용. "Clear"는 NULL 저장
    correction_reason     NVARCHAR(MAX) NULL,               -- MH 감소 등 오류 정정 시 사유. 평시 NULL
    is_deleted            BIT NOT NULL DEFAULT 0,
    deleted_at            DATETIMEOFFSET NULL,
    deleted_by            BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
    version               INT NOT NULL DEFAULT 1,           -- optimistic locking
    supervisor_updated_at DATETIMEOFFSET NULL,              -- 배포 후 Supervisor가 실제 수정한 시각 (MiniPatch 12)
    last_updated_at       DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    last_updated_by       BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    created_at            DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT uq_task_meeting UNIQUE (task_id, meeting_date)
);

CREATE INDEX idx_snap_meeting_deleted ON task_snapshots(meeting_date, is_deleted);
CREATE INDEX idx_snap_task ON task_snapshots(task_id);
```

> MiniPatch 12 원문에서는 `supervisor_updated_at`이 선택 항목이지만, 본 통합본에서는 **NEW 배지 / 배포 후 수정 추적 / RFO 업데이트 배지**를 명확히 구현하기 위해 전용 컬럼을 포함한다.

### 5.4 Future Tables (Schema Only — Phase 2/3)

Phase 0에서 forward compatibility를 위해 생성하되, MVP 코드에서 사용하지 않음. **DDL은 본문에 동봉한다.**

```sql
-- Phase 2: Attendance
CREATE TABLE shift_templates (
    id                 INT IDENTITY(1,1) PRIMARY KEY,
    code               NVARCHAR(20) NOT NULL UNIQUE,
    start_time         TIME NOT NULL,
    end_time           TIME NOT NULL,
    paid_break_minutes INT NOT NULL DEFAULT 60
);

CREATE TABLE shift_assignments (
    id                BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    shift_template_id INT NOT NULL REFERENCES shift_templates(id) ON DELETE NO ACTION,
    [date]            DATE NOT NULL,
    source            NVARCHAR(20) NULL DEFAULT 'IMPORT',
    CONSTRAINT uq_shift_assignment UNIQUE (user_id, [date])
);

CREATE TABLE attendance_events (
    id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    [date]      DATE NOT NULL,
    [type]      NVARCHAR(30) NOT NULL,
    start_time  TIME NULL,
    end_time    TIME NULL,
    minutes_delta INT NULL,
    note        NVARCHAR(MAX) NULL,
    approved_by BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
    created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

-- Phase 3: MH Tracking
CREATE TABLE daily_assignments (
    id             BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    shop_stream_id BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
    [date]         DATE NOT NULL,
    planned_minutes INT NOT NULL CHECK (planned_minutes >= 0),
    note           NVARCHAR(MAX) NULL,
    assigned_by    BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
    created_at     DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

CREATE TABLE worklog_blocks (
    id             BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    shop_stream_id BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
    [date]         DATE NOT NULL,
    started_at     DATETIMEOFFSET NOT NULL,
    ended_at       DATETIMEOFFSET NOT NULL,
    minutes        INT NOT NULL CHECK (minutes > 0),
    created_at     DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
);

CREATE TABLE time_ledger_daily (
    id                    BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id               BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    [date]                DATE NOT NULL,
    capacity_minutes      INT NULL,
    planned_minutes_total INT NULL,
    actual_minutes_total  INT NULL,
    regular_minutes       INT NULL,
    ot_minutes_actual     INT NULL,
    ot_minutes_approved   INT NULL,
    cost_regular          NUMERIC(12,2) NULL,
    cost_ot               NUMERIC(12,2) NULL,
    calc_version          INT NOT NULL DEFAULT 1,
    calculated_at         DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT uq_ledger_daily UNIQUE (user_id, [date])
);

CREATE TABLE ledger_allocations_daily (
    id                        BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id                   BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
    shop_stream_id            BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
    [date]                    DATE NOT NULL,
    allocated_regular_minutes INT NULL,
    allocated_ot_minutes      INT NULL,
    source                    NVARCHAR(20) NULL DEFAULT 'PLANNED'
);
```


---

## 6. 권한 모델(RBAC)

### 6.1 기본 Role

| Role | Description |
|---|---|
| **WORKER** | OT 제출, 자기 OT 조회/취소, Task 조회/수정(shop access에 따라) |
| **SUPERVISOR** | WORKER 권한 + 팀 OT 1차 승인/반려(endorse), 팀 통계, Task MANAGE(shop access에 따라) |
| **ADMIN** | 전체 권한. 사용자/참조데이터/shop 관리, OT 최종 승인/반려(2단계), CSV Import, 감사로그 조회 |

### 6.2 OT 권한 스코프

| Action | WORKER | SUPERVISOR | ADMIN |
|---|---|---|---|
| Submit OT (own) | ✅ | ✅ | ✅ |
| View own OT | ✅ | ✅ | ✅ |
| Cancel own PENDING OT | ✅ | ✅ | ✅ |
| View team OT | ❌ | ✅ (own team) | ✅ (all) |
| **Endorse OT (1단계)** | ❌ | **✅ (own team)** | ❌ |
| **Approve OT (2단계)** | ❌ | ❌ | **✅ (all)** |
| View OT statistics | ❌ | ✅ (own team) | ✅ (all) |
| **CSV Import (Reference Data)** | ❌ | ❌ | **✅** |
| Manage users | ❌ | ❌ | ✅ |
| Manage reference data | ❌ | ❌ | ✅ |
| View audit logs | ❌ | ❌ | ✅ |

- 자기 OT는 승인 불가 (self endorse/approve 금지)
- SUPERVISOR: 같은 team의 PENDING 건만 endorse
- ADMIN: ENDORSED 건만 최종 approve/reject. PENDING 건 직접 처리 불가
- ENDORSED 이후 WORKER 취소 불가 (이미 1차 통과)

### 6.3 Task Manager 권한 스코프

Task는 **Shop 단위 스코프**로 운영한다. 비ADMIN 사용자는 `user_shop_access` 기준으로 접근하고, ADMIN은 bypass로 전체 Shop에 접근한다.

**Task Manager 페이지**: 주로 ADMIN이 사용. SUPERVISOR는 본인 Shop 필터 적용 + 읽기전용.

| 기능 | VIEW | EDIT | MANAGE | ADMIN |
|------|------|------|--------|-------|
| Task Manager 조회 | ✅ (본인 Shop) | ✅ (본인 Shop) | ✅ (본인 Shop) | ✅ (전체) |
| Import RFO | ❌ | ❌ | ❌ | ✅ |
| Create & Assign Task | ❌ | ❌ | ❌ | ✅ |
| Bulk Assign | ❌ | ❌ | ❌ | ✅ |
| Task Manager 상세 패널 | ✅ (조회) | ✅ (조회) | ✅ (조회) | ✅ (조회) |
| Export Report / Audit Log | ❌ | ❌ | ❌ | ✅ |

**Data Entry 페이지**: Supervisor 워크스테이션. 본인 Shop에 **배포된 Task**만 표시.

| 기능 | VIEW | EDIT | MANAGE | ADMIN |
|------|------|------|--------|-------|
| 배포된 Task 조회 | ✅ | ✅ | ✅ | ✅ |
| 상태/MH/remarks/issue 수정 | ❌ | ✅ | ✅ | ✅ |
| Worker 배정 | ❌ | ✅ | ✅ | ✅ |
| Add Task (현재 AC 하위) | ❌ | ✅ | ✅ | ✅ |
| Init-week (carry-over) | ❌ | ❌ | ✅ | ✅ |
| Soft delete / Restore | ❌ | ❌ | ✅ | ✅ |

**ADMIN bypass 규칙**: ADMIN은 `user_shop_access` 테이블 행 없이도 모든 Shop에 MANAGE 접근을 허용한다.

```python
# Pseudocode: shop access check
def check_shop_access(user: User, shop_id: int, required: ShopAccessRole) -> bool:
    if user.has_role("ADMIN"):
        return True  # bypass — no user_shop_access row needed
    access = get_user_shop_access(user.id, shop_id)
    if access is None:
        return False
    return access_level(access.access) >= access_level(required)
    # MANAGE(3) > EDIT(2) > VIEW(1)
```

---

## 7. 비즈니스 규칙(핵심)

### 7.1 OT 규칙

#### 7.1.1 OT 제출 규칙 (본인 신청 + 대리/벌크 제출)
1. `requested_minutes`는 서버에서 `end_time - start_time`(분)으로 계산한다.  
   - 요청에 `requested_minutes`를 보내면 계산값과 일치해야 하며, 불일치 시 **422 VALIDATION_ERROR**
2. `date`는 오늘 또는 미래 (소급 입력 불가. admin override는 추후)
3. 단건(본인/대리 단일) 제출: 동일 `user_id` + `date` + 시간 겹침 OT는 불가 → **422 DUPLICATE_OT**
4. `work_package_id`, `shop_stream_id`는 optional. `shop_stream_id` 제공 시 해당 WP 소속이어야 함
5. `submitted_by` 규칙:
   - 본인 신청: `submitted_by = NULL`, `user_id = current_user.id`
   - 대리 신청(SUPERVISOR+): `submitted_by = current_user.id`, `user_id = 대상 사용자`
6. 벌크(대리) 제출: Request에 `user_ids=[...]`가 오면 user_id별로 개별 OT를 생성한다.
   - 중복(이미 해당 `user_id + date`에 OT 존재)인 대상은 **그 건만 skip** (다른 건은 생성)
7. 권한/스코프:
   - WORKER+: 본인 신청만 가능
   - SUPERVISOR+: 같은 `team` 사용자에 대해서만 대리/벌크 제출 가능 (ADMIN은 전체).  
     위반 시 **403 SHOP_ACCESS_DENIED**
8. 성공 시: status = PENDING, audit_logs CREATE (actor_id = 실제 요청 수행자; 대리 제출은 supervisor)

9. **월 OT 한도 (싱가포르 Employment Act Part IV)**:
   - 한도: 월 72시간 (4,320분)
   - 합산 대상: 해당 월(calendar month)의 **APPROVED + PENDING + ENDORSED** 상태 OT의 `requested_minutes` 합계
   - 제출 시점에 `(기존 합산 + 신규 requested_minutes) > 4,320분`이면 차단
   - 에러: **422 OT_MONTHLY_LIMIT_EXCEEDED**
10. **벌크 제출 시 월 한도**:
   - `user_ids` 중 한도 초과 user → **해당 user만 skip** (reason: `"MONTHLY_LIMIT_EXCEEDED"`)
   - 나머지는 정상 생성
11. **한도 합산에서 CANCELLED는 제외**

#### 7.1.2 OT 승인 규칙 (2단계)

**1단계: SUPERVISOR Endorse (PENDING → ENDORSED / REJECTED)**
1. PENDING 상태만 처리 가능
2. SUPERVISOR: 같은 team만. **ADMIN은 1단계 처리 불가**
3. Self endorse 금지 (`approver_id ≠ ot_request.user_id`)
4. 승인(endorse): `ot_approvals` 행 생성(`stage=ENDORSE`, `action=APPROVE`), status → **ENDORSED**
5. 반려(reject): `ot_approvals` 행 생성(`stage=ENDORSE`, `action=REJECT`), status → **REJECTED**
6. REJECTED는 terminal. 재신청 필요

**2단계: ADMIN Approve (ENDORSED → APPROVED / REJECTED)**
1. ENDORSED 상태만 처리 가능. **PENDING 직접 처리 불가** → **409 INVALID_STATUS**
2. ADMIN만 가능
3. Self approve 금지 (`approver_id ≠ ot_request.user_id`)
4. 승인(approve): `ot_approvals` 행 생성(`stage=APPROVE`, `action=APPROVE`), status → **APPROVED**
5. 반려(reject): `ot_approvals` 행 생성(`stage=APPROVE`, `action=REJECT`), status → **REJECTED**
6. APPROVED, REJECTED는 terminal

**공통**
- 모든 승인/반려는 `audit_logs` UPDATE 기록
- 한 건의 OT에 `ot_approvals` 최대 2행 (1차 + 2차)

#### 7.1.3 OT 취소 규칙
1. **본인만 취소 가능** (ADMIN 포함 타인 취소 불가)
2. **PENDING만 취소 가능** (ENDORSED 이후는 취소 불가 — 이미 1차 통과)
3. ADMIN이 ENDORSED 건을 반려하는 것은 **2단계 reject**로 처리
4. status → CANCELLED, audit_logs UPDATE

### 7.2 Task Manager 규칙(주간 스냅샷)

#### 7.2.1 주간 기준
- meeting_date가 기준일. 저번 주 = meeting_date − 7일
- 향후 Phase 2+에서 prev_date 파라미터 override 가능하게 확장 여지

#### 7.2.2 Carry-over (Init Week)

입력: `(shop_id, meeting_date)`

로직:
1. `prev_date = meeting_date - 7 days`
2. prev_date 스냅샷 중 아래 조건을 모두 만족하는 것을 선택:
   - `task_items.shop_id = :shop_id`
   - `task_snapshots.status != COMPLETED`
   - `task_snapshots.is_deleted = false`
   - **`task_items.is_active = true`** (비활성 task 제외)
3. 위 task들에 대해 meeting_date 스냅샷이 없으면 생성

**복사 정책 (확정):**
| 필드 | 복사 방식 |
|---|---|
| mh_incurred_hours | **이전 주 값 그대로 복사** (누적이므로) |
| status | 이전 주 값 복사 |
| remarks | 이전 주 값 복사 |
| critical_issue | 이전 주 값 복사 |
| has_issue | 이전 주 값 복사 |
| deadline_date | 이전 주 값 복사 (NULL이면 NULL) |
| correction_reason | NULL (새 스냅샷) |
| supervisor_updated_at | NULL (새 배포/수정 추적을 위해 초기화) |
| is_deleted | false (새 스냅샷) |
| version | 1 (새 스냅샷) |

**Idempotent**: UNIQUE(task_id, meeting_date)로 기존 스냅샷이 있으면 skip. 여러 번 눌러도 중복 생성 없음.

#### 7.2.3 Status 표준화
- 저장은 DB enum(NOT_STARTED, IN_PROGRESS, WAITING, COMPLETED)
- API 입력 시 Display Name("Not started" 등)도 서버에서 normalize 수용
- 잘못된 값 → 422 VALIDATION_ERROR

#### 7.2.4 Deadline Optional + Clear
- deadline_date는 NULL 허용
- 기존 값이 있어도 "Clear"로 NULL 저장 가능해야 함
- API에서 `"deadline_date": null`은 명시적 Clear를 의미

#### 7.2.5 Soft delete / Restore
- 삭제: `is_deleted=true` + `deleted_at/deleted_by` 기록. MANAGE 권한 필요
- 복구: `is_deleted=false` + `deleted_at/deleted_by` 초기화. MANAGE 권한 필요
- 기본 리스트는 `is_deleted=false`만 노출
- 회의 콘솔에서 `include_deleted` 토글 제공 (MANAGE)
- 삭제/복구 모두 audit_logs 기록

#### 7.2.6 HasIssue
- 현재 사용자 토글(수동 설정)
- 자동 계산(예: status=WAITING → has_issue=true)은 Phase 2에서 결정
- Power BI 필터/이슈 페이지에 사용

#### 7.2.7 MH 누적 합산 규칙
- mh_incurred_hours는 **누적**. 예: 1주차 1.0h → 2주차 3.0h → 3주차 7.0h
- 주간 투입량은 리포트에서 `(이번 주 - 저번 주)`로 계산
- **MH 감소 제한:**
  - EDIT 권한: 새 값 < 이전 snapshot의 mh → **422 MH_DECREASE_FORBIDDEN**
  - MANAGE 권한: 감소 허용. 단, `correction_reason` 입력 **필수**
  - correction_reason이 입력되면 audit_logs의 after_json에 포함되어 감사 추적 가능
  - 이전 snapshot이 없는 경우(첫 주): 제한 없음



#### 7.2.8 Airline 분류 (SQ vs Third Parties)

Task Manager에서 **"All Airlines / SQ / Third Parties"** 필터를 제공한다.

```
Airline 분류:
  - "SQ" = Singapore Airlines 본사 운항 항공기.
    식별: aircraft.airline 값이 "SQ" 또는 airline 이름이 "Singapore Airlines" (case-insensitive).
  - "Third Parties" = SQ 외 모든 고객사.
    예: Scoot (TR), SilkAir (MI, 현재 SQ에 통합됨), Malaysia Airlines (MH), 기타 MRO 외주 고객.
    airline 필드는 항공사 코드("SQ") 또는 풀네임("Singapore Airlines") 모두 허용.
    분류 로직(is_sq_airline)이 양쪽 모두 case-insensitive로 매칭한다.

필터 동작:
  - "All Airlines" 선택 시: 전체 표시
  - "SQ" 선택 시: SQ aircraft의 task만
  - "Third Parties" 선택 시: SQ 외 aircraft의 task만
```

### 구현 방식 (MVP: 하드코딩, DDL 변경 없음)

aircraft 테이블의 기존 `airline` 필드를 그대로 활용한다.

```python
def is_sq_airline(airline: str | None) -> bool:
    if not airline:
        return False
    a = airline.strip()
    return a.upper() == "SQ" or a.lower() == "singapore airlines"
```

Task 조회(예: Task Manager 리스트)에서의 필터 조건:

```python
# airline_category: "ALL" | "SQ" | "THIRD_PARTIES"
if airline_category == "SQ":
    query = query.filter(
        (func.upper(Aircraft.airline) == "SQ")
        | (func.lower(Aircraft.airline) == "singapore airlines")
    )
elif airline_category == "THIRD_PARTIES":
    query = query.filter(
        ~(
            (func.upper(Aircraft.airline) == "SQ")
            | (func.lower(Aircraft.airline) == "singapore airlines")
        )
    )
# ALL: no filter
```

### Phase 2 확장(선택): airline_categories 테이블

고객사가 늘어나서 세분화가 필요해지면 하드코딩을 테이블 기반으로 전환한다.

```sql
CREATE TABLE airline_categories (
    airline_code NVARCHAR(10) PRIMARY KEY,
    category     NVARCHAR(20) NOT NULL DEFAULT N'Third Parties',
    display_name NVARCHAR(100)
);
INSERT INTO airline_categories VALUES
  (N'SQ', N'SQ', N'Singapore Airlines'),
  (N'TR', N'Third Parties', N'Scoot'),
  (N'MI', N'SQ', N'SilkAir');
```


### 7.3 Task Distribution 워크플로우

```
Admin: Import RFO (Excel/CSV) 또는 Create & Assign
  → task_items 생성 + assigned_supervisor_id + distributed_at 기록
  → 해당 Supervisor의 Data Entry에 자동 표시 (NEW 배지)
  ↓
Supervisor: Data Entry에서 배포된 Task 확인
  → 상태/MH 업데이트 + Worker 배정 (assigned_worker_id)
  → Sub-task 추가 가능 (Add Task 모달)
  → supervisor_updated_at 자동 갱신
  ↓
Admin: Task Manager에서 전체 감사
  → 읽기전용 테이블 (Assigned, Status, MH, Last Updated 확인)
  → 상세 패널에서 Distribution + Remarks + Issue + Audit Trail 조회
```

| # | 규칙 | 설명 |
|---|------|------|
| D1 | 배포 권한 | ADMIN만 Create & Assign, Import RFO, Bulk Assign 가능 |
| D2 | 배포 대상 | Task 생성 시 `assigned_supervisor_id` (필수), Shop (필수) 지정 |
| D3 | Worker 배정 | EDIT+ 권한의 Supervisor가 본인 Shop 내 Worker에게만 배정 가능 |
| D4 | 자동 타임스탬프 | 배포 시 `distributed_at = NOW()`, Supervisor 수정 시 `supervisor_updated_at = NOW()` |
| D5 | NEW 배지 | `distributed_at IS NOT NULL AND supervisor_updated_at IS NULL` → Data Entry에서 NEW 표시 |
| D6 | 미배정 경고 | `assigned_supervisor_id IS NULL` → Task Manager에서 "Unassigned" 빨간 이탤릭 |
| D7 | 배포 추적 | RFO 뷰: `count(assigned_supervisor_id IS NOT NULL) / total` → "3/4 assigned" 배지 |
| D8 | 업데이트 추적 | RFO 뷰: `count(supervisor_updated_at IS NOT NULL) / total` → "2/4 updated" 배지 |
| D9 | Import 형식 | Excel (`.xlsx`) 또는 CSV. 파싱 → 미리보기 → 확인 → DB 기록 + audit_logs |
| D10 | Supervisor Task 추가 | Data Entry에서 Add Task 시 자동으로 `assigned_supervisor_id = 현재 사용자`, 해당 AC/RFO 하위에 생성 |

### 7.4 RFO Detail Lean/Kaizen 메트릭 정의

| 메트릭 | 계산식 | 설명 |
|--------|--------|------|
| Productive Ratio | (Actual MH − Waiting 상태 Task MH) / Actual MH × 100 | 대기 제외 실 생산 비율 |
| OT Ratio | 해당 RFO의 OT Hours / Actual MH × 100 | OT 의존도 |
| First-Time Completion | 재오픈(re-open) 없이 완료 Task 수 / 전체 완료 Task 수 × 100 | 품질 지표 |
| Avg Cycle Time | Σ(완료 snapshot_week − 최초 In Progress snapshot_week) / 완료 Task 수 | 평균 처리 기간 (주 단위) |
| MH Variance | Actual MH − Planned MH | 예산 대비 차이 (음수 = 절감) |
| Blocker Count | status = 'WAITING' AND has_issue = 1 인 Task 수 | 차단 요인 수 |

**Lean 활용 맥락:**
- Productive Ratio < 70% → Waiting 태스크 원인 분석 필요 (Muda 제거)
- OT Ratio > 30% → 인력/일정 재검토 (과부하 신호)
- FTC < 80% → 재작업 원인 분석 (품질 Kaizen)
- Cycle Time 증가 추세 → 병목 식별 필요

---

## 8. API 계약(REST JSON)

### 8.1 공통 규약

#### 8.1.1 인증 (Authentication)
Auth 엔드포인트(login/logout)를 제외한 **모든 API**는 유효한 세션이 없으면 **401**을 반환한다.
권한 부족 시 **403**. FastAPI dependency로 전역 적용.

#### 8.1.2 CSRF
모든 state-changing 요청(POST/PATCH/DELETE)은 CSRF 토큰을 검증한다. (Double Submit Cookie)

**CSRF 구현 상세 (세션 기반):**

1. 서버(로그인 성공 시):
   - 랜덤 CSRF 토큰 생성 (예: 32 byte hex)
   - 세션에 저장: `session["csrf_token"] = token`
   - Set-Cookie: `csrftoken={token}; Path=/; SameSite=Lax; HttpOnly=false`
     - JS에서 읽어야 하므로 `HttpOnly=false`

2. 클라이언트(base.html):
   - `<body>` 태그에 전역 HTMX 헤더 설정(1회 선언):
     ```html
     <body hx-headers='js:{"X-CSRFToken": document.cookie.match(/csrftoken=([^;]+)/)?.[1] || ""}'>
     ```
   - 위 선언 한 번으로 모든 하위 HTMX 요청에 `X-CSRFToken` 헤더가 자동 포함된다.

3. 서버 미들웨어(매 요청):
   - GET/HEAD/OPTIONS → CSRF 검증 skip
   - POST/PATCH/DELETE → `X-CSRFToken` 헤더 값 == `session["csrf_token"]` 검증
   - 불일치 또는 누락 → 403 `CSRF_INVALID`

4. 토큰 갱신:
   - 세션 갱신 시 CSRF 토큰도 재생성 (session fixation 방지)

#### 8.1.3 Pagination (시스템 전역)
모든 목록 API:
- 파라미터: `?page=1&per_page=50`
- 기본: page=1, per_page=50
- 최대: per_page=200 (초과 시 200으로 클램핑)
- 응답 래퍼:
```json
{
  "items": [...],
  "total": 152,
  "page": 1,
  "per_page": 50
}
```

#### 8.1.4 Rate Limiting
인증 사용자 기준 **분당 120 요청**. 초과 시 **429 Too Many Requests**.

Rate limit key:
- 미인증(unauth): IP 기반 — key = `ip:{remote_addr}`
- 인증(auth): user_id 기반 — key = `user:{user_id}`
- 429 응답은 표준 에러 포맷 준수: `{"detail": "...", "code": "RATE_LIMIT"}`

#### 8.1.5 에러 포맷
```json
{
  "detail": "Human-readable error message",
  "code": "MACHINE_READABLE_CODE",
  "field": "optional_field_name"
}
```

#### 8.1.6 에러코드 표

| HTTP | Code | 설명 |
|---|---|---|
| 401 | AUTH_REQUIRED | 인증 필요 |
| 403 | FORBIDDEN | 권한 부족 (일반) |
| 403 | USER_NOT_REGISTERED | Azure AD 로그인은 성공했으나 users 매칭 실패 (관리자 등록 필요) |
| 403 | SELF_ENDORSE | 자기 OT 승인 시도 |
| 403 | OT_WRONG_TEAM | 타 팀 OT 승인 시도 |
| 403 | SHOP_ACCESS_DENIED | Shop 접근 권한 없음 |
| 403 | CSRF_INVALID | CSRF 토큰 무효 |
| 404 | NOT_FOUND | 리소스 없음 |
| 409 | CONFLICT_VERSION | Optimistic lock 충돌 |
| 409 | INVALID_STATUS | 상태 전이 불가 (예: 승인된 OT 재승인) |
| 422 | VALIDATION_ERROR | 입력값 검증 실패 |
| 422 | DUPLICATE_OT | OT 시간 겹침 |
| 422 | OT_MONTHLY_LIMIT_EXCEEDED | 월 72시간 OT 한도 초과 |
| 422 | MH_DECREASE_FORBIDDEN | EDIT 권한으로 MH 감소 시도 |
| 422 | CORRECTION_REASON_REQUIRED | MANAGE 권한 MH 감소 시 correction_reason 누락 |
| 422 | BATCH_VALIDATION_ERROR | 배치 업데이트 검증 실패 (전체 롤백) |
| 422 | USER_HAS_REFERENCES | 사용자 물리 삭제 불가 (참조 존재) |
| 429 | RATE_LIMIT | 요청 제한 초과 |

### 8.2 Authentication (Azure AD OAuth2 + Session)

MiniPatch 9 반영: **자체 ID/PW 로그인 + JWT 발급은 폐기**하고, **Azure AD OAuth2 (Authorization Code Flow)** 를 표준으로 사용한다.

#### 8.2.1 흐름 요약

1. 사용자가 `/login` 접속  
2. 서버가 Azure AD authorize endpoint로 redirect (state/nonce 포함)  
3. Azure AD → `/auth/callback` 으로 authorization code 전달  
4. 서버가 code를 토큰으로 교환(msal/authlib) 후 id_token에서 `oid`, `preferred_username/email`, `name` 추출  
5. 사용자 매칭:
   - 1차: `users.azure_oid == oid`
   - 2차: `users.email == preferred_username/email` 이고 `azure_oid`가 비어 있으면 **azure_oid 자동 매핑**
   - 미등록이면 **403 USER_NOT_REGISTERED** (자동 계정 생성 안 함)
6. 세션 생성(서버사이드 세션) → 홈으로 redirect

세션 만료: 기본 8시간(근무 시간 기준).

#### 8.2.2 엔드포인트

| Method | Path | Description |
|---|---|---|
| GET | /login | Azure AD 로그인으로 redirect |
| GET | /auth/callback | authorization code 처리 + 세션 생성 |
| POST | /logout | 세션 삭제 + (선택) Azure AD 로그아웃으로 redirect |
| GET | /api/auth/me | 현재 사용자 정보 + 역할 |

> 삭제됨: JWT 기반 login/refresh 엔드포인트 (MiniPatch 9)

#### 8.2.3 필수 환경 변수

- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_TENANT_ID`
- `AZURE_REDIRECT_URI` (예: `https://<app-url>/auth/callback`)

#### 8.2.4 세션 payload (권장)

- `user_id`
- `employee_no`
- `display_name`
- `roles` (예: `["WORKER"]`)
- `team` (옵션)

(Shop 권한은 `user_shop_access`로 실시간 판정; 세션에 캐시해도 무방하나, 권한 변경 반영 이슈에 주의)


### 8.3 OT API

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | /api/ot | WORKER+ (self) / SUPERVISOR+ (proxy) | OT 제출 (본인 또는 대리/벌크) |
| GET | /api/ot | WORKER+ | OT 목록 (role-scoped + filters + pagination) |
| GET | /api/ot/{id} | WORKER+ | OT 상세 |
| PATCH | /api/ot/{id}/cancel | WORKER+ | 본인 PENDING OT 취소 |
| POST | /api/ot/{id}/endorse | SUPERVISOR | OT 1차 승인/반려 (PENDING → ENDORSED/REJECTED) |
| POST | /api/ot/{id}/approve | ADMIN | OT 최종 승인/반려 (ENDORSED → APPROVED/REJECTED) |
| GET | /api/ot/export/csv | SUPERVISOR+ | OT CSV Export |


#### 8.3.1 POST /api/ot — OT 제출 (본인 / 대리 / 벌크)

- **본인 신청(기본)**: `user_ids` 없이 호출 → `user_id = current_user.id`, `submitted_by = NULL`
- **대리/벌크 신청(SUPERVISOR+)**: `user_ids=[...]` 포함 → 각 user_id에 대해 OT 생성, `submitted_by = current_user.id`

**Request (본인 신청)**

```json
{
  "date": "2026-02-26",
  "start_time": "17:00",
  "end_time": "19:30",
  "requested_minutes": 150,
  "reason_code": "BACKLOG",
  "reason_text": "Need to complete wing inspection",
  "work_package_id": 12,
  "shop_stream_id": 5
}
```

- `requested_minutes`는 **옵션**.  
  - 미입력 시 서버가 `(end_time - start_time)`으로 계산해 저장  
  - 입력 시 계산값과 불일치하면 **422 VALIDATION_ERROR**

- **월 72h OT 한도 검증(4,320분)**:
  - 서버는 제출 전에 해당 월(calendar month)의 **(PENDING + ENDORSED + APPROVED)** `requested_minutes` 합계를 계산한다.
  - `(기존 합산 + 신규 minutes) > 4,320` 이면 **422 OT_MONTHLY_LIMIT_EXCEEDED**
  - 벌크 제출에서는 한도 초과 user만 skip (reason=`"MONTHLY_LIMIT_EXCEEDED"`)

**Response (201)**

```json
{
  "id": 42,
  "user_id": 7,
  "user_name": "Ali bin Ahmad",
  "submitted_by": null,
  "submitted_by_name": null,
  "date": "2026-02-26",
  "start_time": "17:00",
  "end_time": "19:30",
  "requested_minutes": 150,
  "reason_code": "BACKLOG",
  "reason_text": "Need to complete wing inspection",
  "work_package_id": 12,
  "shop_stream_id": 5,
  "status": "PENDING",
  "created_at": "2026-02-26T10:30:00+08:00"
}
```

**Request (대리 신청 — bulk)**

```json
{
  "date": "2026-02-26",
  "start_time": "17:00",
  "end_time": "19:30",
  "reason_code": "BACKLOG",
  "reason_text": "Bulk OT request for shop backlog",
  "user_ids": [10, 11, 12, 13]
}
```

**Response (200)**

```json
{
  "created": [
    { "id": 101, "user_id": 10, "submitted_by": 3, "status": "PENDING" },
    { "id": 102, "user_id": 11, "submitted_by": 3, "status": "PENDING" }
  ],
  "skipped": [
    { "user_id": 12, "reason": "DUPLICATE_DATE" },
    { "user_id": 13, "reason": "MONTHLY_LIMIT_EXCEEDED" }
  ],
  "created_count": 2,
  "skipped_count": 2
}
```

**권한/중복 처리(중요)**

- WORKER+: **본인 신청만 가능**
- SUPERVISOR+: **같은 team 사용자에 대해서만** 대리/벌크 제출 가능 (ADMIN은 전체)  
  - 위반 시 **403 SHOP_ACCESS_DENIED**
- 단건 제출 중복: 동일 `user_id + date`에서 시간 겹침 OT는 **422 DUPLICATE_OT**
- 벌크 제출 중복: 대상 user가 해당 `user_id + date`에 이미 OT가 있으면 **그 건만 skip**
- 월 한도 초과: 단건은 **422 OT_MONTHLY_LIMIT_EXCEEDED**, 벌크는 해당 user만 skip (reason=`"MONTHLY_LIMIT_EXCEEDED"`)

> Audit: 생성/수정 actor는 **항상 요청을 수행한 사용자**(대리 제출 시 supervisor)이며, 대리 제출은 `submitted_by`로 추적한다.

#### 8.3.2 GET /api/ot — OT 목록

```
Query params:
  ?status=PENDING
  ?date_from=2026-02-01
  ?date_to=2026-02-28
  ?user_id=7                (SUPERVISOR/ADMIN only)
  ?page=1&per_page=20
```

```json
{
  "items": [ /* OT request objects (submitted_by/submitted_by_name 포함) */ ],
  "total": 42,
  "page": 1,
  "per_page": 20
}
```

#### 8.3.3 POST /api/ot/{id}/endorse — 1차 승인 (SUPERVISOR)

Auth: **SUPERVISOR only** (같은 team만). **ADMIN은 1단계 endorse 불가**.

**Request:**
```json
{
  "action": "APPROVE",
  "comment": "Checked, forwarding to admin"
}
```

**Response (200):**
```json
{
  "ot_request_id": 42,
  "stage": "ENDORSE",
  "action": "APPROVE",
  "approver_id": 3,
  "approver_name": "Supervisor Lee",
  "comment": "Checked, forwarding to admin",
  "acted_at": "2026-02-26T11:00:00+08:00",
  "ot_request": { "status": "ENDORSED", "...": "..." }
}
```

**에러:**

| HTTP | Code | 조건 |
|---|---|---|
| 403 | SELF_ENDORSE | approver == OT 신청자 |
| 403 | OT_WRONG_TEAM | 타 팀 OT |
| 403 | FORBIDDEN | 호출자가 SUPERVISOR가 아님(또는 ADMIN) |
| 409 | INVALID_STATUS | status ≠ PENDING |

#### 8.3.3a POST /api/ot/{id}/approve — 최종 결재 (ADMIN)

Auth: **ADMIN only**.

**Request:**
```json
{
  "action": "APPROVE",
  "comment": "Final approval granted"
}
```

**Response (200):**
```json
{
  "ot_request_id": 42,
  "stage": "APPROVE",
  "action": "APPROVE",
  "approver_id": 1,
  "approver_name": "Admin User",
  "comment": "Final approval granted",
  "acted_at": "2026-02-26T12:00:00+08:00",
  "ot_request": { "status": "APPROVED", "...": "..." }
}
```

**에러:**

| HTTP | Code | 조건 |
|---|---|---|
| 403 | SELF_ENDORSE | approver == OT 신청자 |
| 403 | FORBIDDEN | 호출자가 ADMIN이 아님 |
| 409 | INVALID_STATUS | status ≠ ENDORSED |


#### 8.3.4 GET /api/ot/export/csv — CSV Export (MVP 포함)

```
GET /api/ot/export/csv?date_from=2026-01-01&date_to=2026-02-28&status=APPROVED
Auth: SUPERVISOR+ (본인 team), ADMIN (전체)
```

- 추가 필터:
  - `shop_id` (optional): Task Manager `shops.id` 기준.  
    제공 시 **해당 shop_id에 user_shop_access가 존재하는 사용자**의 OT만 포함(교차 필터)
  - `user_id` (optional, ADMIN only): 특정 사용자만

**Response**
- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="ot_export_YYYYMMDD_YYYYMMDD.csv"`

**CSV Columns**
- `ot_id`
- `user_name`
- `user_employee_no`
- `date`
- `start_time`
- `end_time`
- `minutes`
- `reason_code`
- `reason_text`
- `status`
- `submitted_by_name`
- `endorsed_by_name`
- `endorsed_at`
- `approved_by_name`
- `approved_at`

### 8.4 Task API

#### 8.4.1 Init week (carry-over)
```
POST /api/tasks/init-week
Auth: MANAGE (on target shop)
```
```json
// Request
{ "meeting_date": "2026-02-26", "shop_id": 3 }

// Response (200)
{
  "meeting_date": "2026-02-26",
  "shop_id": 3,
  "created_count": 5,
  "skipped_count": 2
}
```

#### 8.4.2 List snapshots
```
GET /api/tasks/snapshots?meeting_date=2026-02-26&shop_id=3&include_deleted=false&page=1&per_page=50
Auth: VIEW+ (on target shop)
```

Query params:
  - `meeting_date`: YYYY-MM-DD (required)
  - `shop_id`: int (required)
  - `work_package_id`: int (optional) — RFO 기준 필터
  - `assigned_supervisor_id`: int (optional) — 담당 Supervisor 필터
  - `aircraft_id`: int (optional)
  - `status`: task_status (optional)
  - `has_issue`: bool (optional)
  - `include_deleted`: bool (default=false)
  - `airline_category`: ALL (default) | SQ | THIRD_PARTIES (optional)
  - `page`, `per_page`: pagination

```json
// Response (200)
{
  "items": [
    {
      "snapshot_id": 88,
      "task_id": 15,
      "meeting_date": "2026-02-26",
      "aircraft_id": 12,
      "work_package_id": 1,
      "rfo_no": "1200000101",
      "ac_reg": "9V-SMA",
      "shop_id": 3,
      "shop_name": "Sheet Metal Shop",
      "assigned_supervisor_id": 3,
      "assigned_supervisor_name": "Supervisor Lee",
      "assigned_worker_id": 10,
      "assigned_worker_name": "Worker Ali",
      "distributed_at": "2026-03-09T08:15:00+08:00",
      "planned_mh": 15.0,
      "task_text": "Replace panel L1-42",
      "status": "IN_PROGRESS",
      "mh_incurred_hours": 12.5,
      "remarks": "Waiting parts",
      "critical_issue": "AOG risk",
      "has_issue": true,
      "deadline_date": "2026-03-15",
      "correction_reason": null,
      "is_deleted": false,
      "version": 3,
      "supervisor_updated_at": "2026-03-09T10:30:00+08:00",
      "last_updated_at": "2026-03-09T10:30:00+08:00",
      "last_updated_by": 5,
      "is_active": true
    }
  ],
  "total": 23,
  "page": 1,
  "per_page": 50
}
```

#### 8.4.3 Create task + snapshot
```
POST /api/tasks
Auth: EDIT+ (on target shop)
```
```json
// Request
{
  "meeting_date": "2026-02-26",
  "shop_id": 3,
  "aircraft_id": 12,
  "work_package_id": 1,
  "assigned_supervisor_id": 3,
  "planned_mh": 15.0,
  "task_text": "Replace panel L1-42",
  "status": "NOT_STARTED",
  "mh_incurred_hours": 0,
  "deadline_date": null,
  "remarks": "",
  "critical_issue": "",
  "has_issue": false
}

// Response (201)
{
  "task_id": 15,
  "snapshot_id": 88,
  "meeting_date": "2026-02-26",
  "shop_id": 3,
  "aircraft_id": 12,
  "work_package_id": 1,
  "rfo_no": "1200000101",
  "assigned_supervisor_id": 3,
  "assigned_worker_id": null,
  "distributed_at": "2026-03-09T08:15:00+08:00",
  "planned_mh": 15.0,
  "task_text": "Replace panel L1-42",
  "status": "NOT_STARTED",
  "mh_incurred_hours": 0,
  "deadline_date": null,
  "remarks": "",
  "critical_issue": "",
  "has_issue": false,
  "correction_reason": null,
  "version": 1,
  "supervisor_updated_at": null,
  "last_updated_at": "2026-03-09T08:15:00+08:00",
  "last_updated_by": 1
}
```

- `assigned_supervisor_id`는 ADMIN의 Task Manager `Create & Assign` 플로우에서 사용한다. 값이 주어지면 서버는 `distributed_at = NOW()`를 자동 기록한다.
- Data Entry에서 Supervisor가 하위 Task를 추가하는 경우, 서버는 `assigned_supervisor_id = current_user.id`를 자동 적용할 수 있다. 필요 시 `assigned_worker_id`, `planned_mh`를 함께 받을 수 있다.

#### 8.4.4 Update snapshot
```
PATCH /api/tasks/snapshots/{snapshot_id}
Auth: EDIT+ (on target shop)
```
```json
// Request — version 필수 (optimistic locking)
{
  "version": 3,
  "status": "IN_PROGRESS",
  "mh_incurred_hours": 12.5,
  "deadline_date": null,
  "remarks": "Waiting parts",
  "critical_issue": "AOG risk",
  "has_issue": true,
  "correction_reason": null
}

// Response (200)
{
  "snapshot_id": 42,
  "version": 4,
  "status": "IN_PROGRESS",
  "mh_incurred_hours": 12.5,
  "deadline_date": null,
  "remarks": "Waiting parts",
  "critical_issue": "AOG risk",
  "has_issue": true,
  "correction_reason": null,
  "last_updated_at": "2026-02-26T10:30:00+08:00",
  "last_updated_by": 5,
  "supervisor_updated_at": "2026-03-09T10:30:00+08:00"
}

// Error — version 충돌 (409)
{
  "detail": "Snapshot modified by another user. Reload and retry.",
  "code": "CONFLICT_VERSION",
  "current_version": 4
}

// Error — MH 감소 (EDIT 권한) (422)
{
  "detail": "MH decrease not allowed with EDIT permission. Use MANAGE or correct via meeting console.",
  "code": "MH_DECREASE_FORBIDDEN",
  "field": "mh_incurred_hours"
}

// Error — MH 감소 (MANAGE 권한, correction_reason 누락) (422)
{
  "detail": "correction_reason is required when decreasing mh_incurred_hours.",
  "code": "CORRECTION_REASON_REQUIRED",
  "field": "correction_reason"
}
```

- Supervisor가 Data Entry에서 수정한 경우 서버는 `supervisor_updated_at = NOW()`를 함께 갱신한다.

#### 8.4.5 Batch update snapshots
```
PATCH /api/tasks/snapshots/batch
Auth: EDIT+ (on target shop)
```
All-or-nothing: **하나라도 오류(version 충돌, 검증 실패) 시 전체 롤백.**
```json
// Request
{
  "updates": [
    { "snapshot_id": 42, "version": 3, "status": "IN_PROGRESS", "mh_incurred_hours": 10 },
    { "snapshot_id": 43, "version": 1, "status": "COMPLETED", "mh_incurred_hours": 8 }
  ]
}

// Response — 전체 성공 (200)
{
  "items": [
    { "snapshot_id": 42, "version": 4, ... },
    { "snapshot_id": 43, "version": 2, ... }
  ]
}

// Response — 1건이라도 실패 → 전체 롤백 (422 또는 409)
{
  "detail": "Batch update failed. All changes rolled back.",
  "code": "BATCH_VALIDATION_ERROR",
  "errors": [
    { "snapshot_id": 43, "code": "CONFLICT_VERSION", "current_version": 2 }
  ]
}
```

#### 8.4.6 Deactivate / Reactivate task
```
PATCH /api/tasks/{task_id}/deactivate     Auth: MANAGE
PATCH /api/tasks/{task_id}/reactivate     Auth: MANAGE
```
```json
// Response (200)
{
  "task_id": 15,
  "is_active": false,
  "deactivated_at": "2026-02-26T11:00:00+08:00",
  "deactivated_by": 5
}
```

#### 8.4.7 Soft delete snapshot
```
PATCH /api/tasks/snapshots/{snapshot_id}/delete
Auth: MANAGE (on target shop)
```

```json
// Request
{ "version": 3 }
```

```json
// Response (200)
{
  "snapshot_id": 42,
  "is_deleted": true,
  "version": 4,
  "deleted_at": "2026-02-26T10:30:00+08:00",
  "deleted_by": 5
}
```

- 공통: audit_logs 기록 (entity_type=`task_snapshot`, action=`DELETE`)
- version은 성공 시 **+1** 증가

#### 8.4.8 Restore snapshot
```
PATCH /api/tasks/snapshots/{snapshot_id}/restore
Auth: MANAGE (on target shop)
```

```json
// Request
{ "version": 4 }
```

```json
// Response (200)
{
  "snapshot_id": 42,
  "is_deleted": false,
  "version": 5,
  "deleted_at": null,
  "deleted_by": null
}
```

- 공통: audit_logs 기록 (entity_type=`task_snapshot`, action=`RESTORE`)
- version은 성공 시 **+1** 증가

#### 8.4.9 GET /api/tasks/export/csv — CSV Export (MVP 포함)

```
GET /api/tasks/export/csv?meeting_date=2026-02-26&shop_id=3&include_deleted=false
Auth: VIEW+ (on target shop)
```

**Response**
- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="tasks_export_YYYYMMDD_shop{shop_id}.csv"`

**CSV Columns**
- `snapshot_id`
- `meeting_date`
- `shop_id`
- `shop_name`
- `ac_reg`
- `work_package_id`
- `rfo_no`
- `assigned_supervisor_name`
- `assigned_worker_name`
- `distributed_at`
- `planned_mh`
- `task_text`
- `status`
- `mh_incurred_hours`
- `weekly_mh_delta`
- `remarks`
- `has_issue`
- `deadline_date`
- `is_deleted`
- `updated_by_name`
- `updated_at`


### 8.5 Admin API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/shops | ADMIN | Shop 목록 |
| POST | /api/shops | ADMIN | Shop 생성 |
| PATCH | /api/shops/{id} | ADMIN | Shop 수정 |
| GET | /api/shop-access | ADMIN | Shop access 목록 |
| POST | /api/shop-access | ADMIN | Shop access 부여 |
| DELETE | /api/shop-access/{id} | ADMIN | Shop access 삭제 |

### 8.6 Reference Data API (Base A 복원)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/aircraft | WORKER+ | Aircraft 목록 |
| POST | /api/aircraft | ADMIN | Aircraft 생성 |
| GET | /api/work-packages | WORKER+ | RFO(WP) 목록 (filterable by aircraft_id, rfo_no) |
| POST | /api/work-packages | ADMIN | RFO(WP) 생성 (rfo_no 포함) |
| GET | /api/shop-streams | WORKER+ | Shop stream 목록 (filterable by WP) |
| POST | /api/shop-streams | ADMIN | Shop stream 생성 |
| POST | /api/reference/import/csv | ADMIN | CSV Import (Reference Data 일괄 등록) |


#### 8.6.1 POST /api/work-packages — rfo_no 포함

- Auth: **ADMIN**

```json
// Request
{ "aircraft_id": 1, "rfo_no": "1200000101", "title": "C-Check 2026-Q1", "start_date": "2026-01-10", "end_date": "2026-03-30", "priority": 1 }
```

- `rfo_no`는 **NULL 허용**. (NULL이 아닌 경우 filtered unique index로 중복 방지)

#### 8.6.2 GET /api/work-packages — rfo_no 검색

- Auth: WORKER+
- Query: `?aircraft_id=&rfo_no=`

#### 8.6.3 POST /api/reference/import/csv — Reference Data 일괄 등록

- Auth: **ADMIN**
- `Content-Type: multipart/form-data`
- file size limit: **5MB**

지원 import 대상:

| entity_type | 필수 컬럼 | 선택 컬럼 |
|---|---|---|
| `aircraft` | ac_reg | airline, status |
| `work_package` | aircraft_ac_reg, title | rfo_no, start_date, end_date, priority |
| `shop_stream` | work_package_rfo_no (또는 title), shop_code | — |

Response (200) 예시:
```json
{
  "entity_type": "work_package",
  "created_count": 8,
  "skipped_count": 2,
  "errors": [
    {"row": 5, "reason": "aircraft_ac_reg '9V-XXX' not found"}
  ]
}
```

규칙:
- 기존 데이터와 중복(`rfo_no` 또는 `ac_reg` unique)이면 **skip**
- FK 참조 실패(존재하지 않는 aircraft 등)면 해당 행만 error, 나머지는 처리
- 모든 성공 건은 `audit_logs` 기록

### 8.7 Statistics API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/stats/ot-summary | SUPERVISOR+ | OT 요약 (date range, team filter) |
| GET | /api/stats/ot-monthly-usage | SUPERVISOR+ / ADMIN | 월간 OT 사용량(72h limit) |
| GET | /api/rfo/{work_package_id}/summary | SUPERVISOR+ / ADMIN | RFO 기준 Task + OT 통합 요약 |



#### 8.7.1 GET /api/stats/ot-monthly-usage — 월간 OT 현황(72h)

```
GET /api/stats/ot-monthly-usage?month=2026-03
Auth: SUPERVISOR+ (본인 team) / ADMIN (전체)
```

- Query:
  - `month`: YYYY-MM (default: 현재 월)

```json
// Response (200)
{
  "month": "2026-03",
  "limit_minutes": 4320,
  "users": [
    {
      "user_id": 10,
      "name": "Worker Ali",
      "employee_no": "E010",
      "used_minutes": 3480,
      "remaining_minutes": 840,
      "pending_minutes": 150,
      "usage_pct": 80.6
    }
  ]
}
```

#### 8.7.2 GET /api/rfo/{work_package_id}/summary — RFO 기준 통합 조회

```
GET /api/rfo/{work_package_id}/summary
Auth: SUPERVISOR+ (본인 team) / ADMIN (전체)
```

```json
// Response (200)
{
  "work_package_id": 1,
  "rfo_no": "1200000101",
  "title": "C-Check 2026-Q1",
  "aircraft": { "ac_reg": "9V-SMA", "airline": "Singapore Airlines" },
  "tasks": {
    "total": 12,
    "by_status": {
      "NOT_STARTED": 2,
      "IN_PROGRESS": 5,
      "WAITING": 3,
      "COMPLETED": 2
    },
    "total_mh": 156.5
  },
  "ot": {
    "total_requests": 8,
    "total_approved_minutes": 1200,
    "by_status": {
      "PENDING": 1,
      "ENDORSED": 2,
      "APPROVED": 4,
      "REJECTED": 1
    }
  }
}
```

### 8.8 Users API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/users | ADMIN | 사용자 목록 |
| POST | /api/users | ADMIN | 사용자 생성 |
| PATCH | /api/users/{id} | ADMIN | 사용자 수정 (team, is_active, roles) |
| DELETE | /api/users/{id} | ADMIN | 사용자 물리 삭제 (조건부) |

#### 8.8.1 DELETE /api/users/{id} — 사용자 물리 삭제(조건부)

- Auth: **ADMIN**
- 조건: §0.2 원칙 #5의 **HARD DELETE 허용 조건**을 모두 만족해야 함
- 성공 (200)

```json
{ "deleted": true, "user_id": 42 }
```

- 실패 (422)

```json
{
  "detail": "User has references. Use deactivation instead.",
  "code": "USER_HAS_REFERENCES"
}
```

> 운영 기본 정책은 비활성화(`is_active=false`)이며, 물리 삭제는 **예외 케이스(참조 0건)**에서만 허용한다.


---

### 8.9 Config API (Admin Settings)

Admin Settings 화면(회의 기준일, 알림 토글/수신자/템플릿 등)을 위한 설정 저장/조회 API.

> DB: `system_config` 테이블 사용 (§5.2 참고)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/config | ADMIN | 설정 목록 조회 |
| PATCH | /api/config | ADMIN | 설정 일괄 수정 |
| GET | /api/config/{key} | SUPERVISOR+ (meeting_current_date만) / ADMIN (그 외) | 단일 설정 조회 |

#### 8.9.1 GET /api/config — 설정 목록

```
GET /api/config
Auth: ADMIN
```

```json
// Response (200)
{
  "configs": [
    { "key": "meeting_current_date", "value": "2026-02-26", "updated_at": "2026-03-02T00:00:00+08:00" }
  ]
}
```

#### 8.9.2 PATCH /api/config — 설정 일괄 수정

```
PATCH /api/config
Auth: ADMIN
```

```json
// Request
{
  "configs": [
    { "key": "teams_enabled", "value": "false" },
    { "key": "teams_recipients", "value": "#cis-sheet-metal" }
  ]
}
```

```json
// Response (200)
{ "updated": 2 }
```

- Side effect: 변경된 key마다 `audit_logs`에 before/after 기록 (entity_type=`system_config`, action=`UPDATE`)
- Unknown key: **404 NOT_FOUND** (권장) 또는 서버 정책에 따라 자동 생성 금지

#### 8.9.3 GET /api/config/{key} — 단일 설정

```
GET /api/config/{key}
Auth:
  - SUPERVISOR+ : key = "meeting_current_date" 인 경우에만 허용
  - ADMIN       : 그 외 모든 key
```

```json
// Response (200)
{ "key": "meeting_current_date", "value": "2026-02-26" }
```

### 8.10 Task Distribution API

| Method | Path | Auth | 설명 |
|--------|------|------|------|
| POST | `/api/tasks/import` | ADMIN | RFO Excel/CSV 업로드 → 파싱 → 미리보기 JSON 반환 |
| POST | `/api/tasks/import/confirm` | ADMIN | 미리보기 확인 후 DB 저장 (all-or-nothing) |
| POST | `/api/tasks/{id}/assign` | ADMIN | 단건 Task → Supervisor 배포 (`assigned_supervisor_id` + `distributed_at` 설정) |
| POST | `/api/tasks/bulk-assign` | ADMIN | 다건 Task → Supervisor 일괄 배포 |
| PATCH | `/api/tasks/{id}/assign-worker` | EDIT+ | Task → Worker 배정 (`assigned_worker_id` 설정). 본인 Shop 내 Worker만 허용 |

**POST /api/tasks/import 요청**
```json
// multipart/form-data
{
  "file": "<.xlsx or .csv file>"
}
```

**POST /api/tasks/import 응답 (200)**
```json
{
  "preview": [
    {"row": 1, "ac_reg": "9V-SMA", "rfo_no": "1200000101", "description": "Replace panel L1-42", "planned_mh": 15.0, "valid": true},
    {"row": 2, "ac_reg": "9V-SMA", "rfo_no": "INVALID", "description": "...", "planned_mh": 0, "valid": false, "error": "RFO not found"}
  ],
  "valid_count": 4,
  "error_count": 1
}
```

**POST /api/tasks/import/confirm 규칙**
- Preview payload 기준으로 DB에 **all-or-nothing** 저장한다.
- 생성된 Task는 `assigned_supervisor_id`, `distributed_at`, `planned_mh`를 반영할 수 있다.
- 성공 시 `audit_logs`에 CREATE/IMPORT 이력을 남긴다.

**POST /api/tasks/{id}/assign 요청**
```json
{
  "assigned_supervisor_id": 3,
  "shop_id": 1
}
```

**POST /api/tasks/bulk-assign 요청**
```json
{
  "task_ids": [1, 2, 5, 8],
  "assigned_supervisor_id": 3
}
```

**PATCH /api/tasks/{id}/assign-worker 요청**
```json
{
  "assigned_worker_id": 10
}
```

### 8.11 RFO Metrics API

| Method | Path | Auth | 설명 |
|--------|------|------|------|
| GET | `/api/rfo/{id}/metrics` | SUPERVISOR+ | Lean/Kaizen 메트릭 (Productive Ratio, OT Ratio, FTC, Cycle Time, Variance) |
| GET | `/api/rfo/{id}/blockers` | SUPERVISOR+ | Active Blockers 목록 |
| GET | `/api/rfo/{id}/worker-allocation` | SUPERVISOR+ | Worker별 MH/태스크 분포 |
| GET | `/api/rfo/{id}/burndown` | SUPERVISOR+ | 주간별 MH 누적/잔여 데이터 |

**GET /api/rfo/{id}/metrics 응답 (200)**
```json
{
  "work_package_id": 1,
  "total_tasks": 12,
  "planned_mh": 180.0,
  "actual_mh": 156.5,
  "mh_variance": -23.5,
  "ot_hours": 42.0,
  "ot_ratio_pct": 26.8,
  "productive_ratio_pct": 73.2,
  "first_time_completion_pct": 83.3,
  "avg_cycle_time_weeks": 3.2,
  "blocker_count": 3,
  "unassigned_count": 2
}
```

### 8.12 OT Statistics API 확장

| Method | Path | Auth | 설명 |
|--------|------|------|------|
| GET | `/api/stats/ot-by-reason` | SUPERVISOR+ | Reason Code별 OT 시간 집계 |
| GET | `/api/stats/ot-weekly-trend` | SUPERVISOR+ | 주간별 OT 추세 |

**GET /api/stats/ot-by-reason 응답 (200)**
```json
{
  "month": "2026-03",
  "team": "TEAM-A",
  "breakdown": [
    {"reason_code": "BACKLOG", "hours": 73, "pct": 45.1},
    {"reason_code": "SCHEDULE_PRESSURE", "hours": 45, "pct": 27.8},
    {"reason_code": "AOG", "hours": 24, "pct": 14.8},
    {"reason_code": "MANPOWER_SHORTAGE", "hours": 12, "pct": 7.4},
    {"reason_code": "OTHER", "hours": 8, "pct": 4.9}
  ]
}
```

**GET /api/stats/ot-weekly-trend 응답 (200)**
```json
{
  "month": "2026-03",
  "weeks": [
    {"week": 1, "label": "Mar 02–08", "hours": 28},
    {"week": 2, "label": "Mar 09–15", "hours": 42},
    {"week": 3, "label": "Mar 16–22", "hours": 56},
    {"week": 4, "label": "Mar 23–29", "hours": 36}
  ]
}
```

---

## 9. UI (ERP 웹: Jinja2 + HTMX)

### 9.1 화면 목록

| Screen | Route | Role | Description | 변경 |
|--------|-------|------|-------------|------|
| Login | /login | All | Azure AD 로그인으로 redirect | 기존 |
| Dashboard | /dashboard | All | Role별 대시보드 + **RFO Progress 위젯** (기존 Recent Tasks 대체) | **수정** |
| OT Submit | /ot/new | WORKER+ | OT 제출 + **Technician Roster 검색** 추가 | **수정** |
| OT List | /ot | WORKER+ | OT 목록 (필터 + 페이지네이션, role-scoped) | 기존 |
| OT Detail | /ot/{id} | WORKER+ | OT 상세 + 승인 이력(1차/2차) + endorse/approve 액션 | 기존 |
| OT Stats | /stats/ot | SUPERVISOR+ | **Individual OT vs 72h** (전폭, 6명) + **OT by Reason** + **Weekly Trend** | **대폭 확장** |
| Admin OT Approve | /admin/ot-approve | ADMIN | ENDORSED 건 최종 결재 대기열 | 기존 |
| **Task Manager** | /tasks | ADMIN 주사용, SUP 읽기 | 배포/감사 허브: Import RFO, Create & Assign, Bulk Assign, Table/Kanban/RFO 3뷰, 상세 오버레이 패널, Pagination | **명칭+역할 변경** (기존 Meeting Console) |
| **Data Entry** | /tasks/entry | SUPERVISOR 주사용 | Supervisor 워크스테이션: 배포된 Task 업데이트, Worker 배정, Add Task, Quick Update 카드 | **역할 재정의** |
| Task Detail | /tasks/{task_id} | VIEW+ | Task 상세 (스냅샷 히스토리) | 기존 |
| **RFO Detail** | /rfo/{id} | SUPERVISOR+ | **검색 콤보박스**, Summary Strip, 6 KPI, MH Burndown, Efficiency Metrics, Blockers, Worker Allocation | **전면 재설계** |
| Shop Admin | /admin/shops | ADMIN | Shop CRUD | 기존 |
| Shop Access | /admin/shop-access | ADMIN | user_shop_access 관리 | 기존 |
| User Admin | /admin/users | ADMIN | **Add User 모달** + **Edit User 모달** 포함 사용자 관리 | **수정** |
| Reference Admin | /admin/reference | ADMIN | Aircraft, RFO(WP), shop stream 관리 + **CSV Import** | **수정** |
| System Settings | /admin/settings | ADMIN | **Snapshot Week Config** + 알림/수신자/템플릿 설정 | **수정** |

### 9.1.1 Task Manager (기존 Meeting Console) 변경 상세

| 항목 | 변경 내용 |
|------|----------|
| 페이지 제목 | "Meeting Console" → "Task Manager" |
| 부제 | "Create, distribute, and audit tasks across shops" |
| 주 사용자 | ADMIN (SUPERVISOR는 본인 Shop 필터 + 읽기전용) |
| 헤더 버튼 | `Init Week` + `New Task` → `Import RFO` + `Create & Assign` |
| 필터 추가 | `All Supervisors` 드롭다운 |
| 테이블 (7열) | AC/RFO (합침), Task Description (remarks+issue 인라인), Assigned (Supervisor 아바타), Status (읽기전용 badge), MH (Δ 포함), Deadline (last updated 포함) |
| 테이블 | `table-layout: fixed` + 컬럼별 width 지정, 10행 표시 |
| Pagination | 하단바 통합: "1–10 of 23 tasks" + 페이지 버튼 + Export/Audit |
| Bulk Actions | "Assign To…" 버튼 (다건 Supervisor 배정) |
| 상세 패널 | `position: fixed` 오버레이 (전체 높이), backdrop 클릭/✕/같은 행 재클릭으로 닫기 |
| 상세 패널 내용 | Distribution (배정자/배포일/최종업데이트) + Remarks + Active Issue + MH History + Audit Trail |
| Create & Assign 모달 | AC Reg: free text, RFO No.: free text, Shop/Supervisor 배정, Deadline, Initial MH |
| 칸반 뷰 | 10카드 (테이블 동기화), `overflow-y:auto` 컬럼별 스크롤, data-remarks/issue/updated 속성 |
| 칸반 상세 | Remarks + Active Issue (조건부 표시) + Distribution 섹션 추가 |
| RFO 뷰 | 2줄 헤더, 배포 추적 배지, 행 클릭→상세 패널, `flex-shrink:0` + 세로 스크롤, 카드 배경 `#fff` (구분) |
| 3뷰 동기화 | Table 10행 = Kanban 10카드 = RFO 10태스크 |

### 9.1.2 Data Entry 변경 상세

| 항목 | 변경 내용 |
|------|----------|
| 좌측 패널 헤더 | "Aircraft" → "My Assigned Tasks" + Shop/Supervisor 컨텍스트 |
| 좌측 패널 필터 | Shop 선택 제거 → Status 필터 (All / Not Started / In Progress / Waiting / Completed) |
| AC 카드 | NEW 배지, needs update 경고, ✓ up to date 상태 |
| 태스크 목록 | Worker 배정 표시, 최종 업데이트 시간, NEW/NEEDS UPDATE 배지, 신규 배포 배경 하이라이트 |
| Add Task 모달 | Description, Status, Deadline, Estimated MH, Assign Worker, Remarks |
| 편집 패널 | `max-w-4xl mx-auto`, Quick Update + Worker Assignment 2열, Details & Remarks 3열 |
| Quick Update | 금색 좌측 보더 강조, Status + MH 최상단 |
| Worker Assignment | 별도 카드, 드롭다운 + "+ Add" + 현재 배정자 표시 |

### 9.1.3 OT Dashboard 변경

| 항목 | 변경 내용 |
|------|----------|
| Individual Monthly OT | 반폭→전폭, 3명→6명, 2열 그리드, 잔여 시간/남은 횟수, 색상 범례, 팀 평균 |
| 제목 | "Team Monthly OT Usage" → "Individual Monthly OT vs 72h Limit" |
| OT by Reason Code | 신규: 사유별 막대 차트 |
| Weekly OT Trend | 신규: W1~W4 주간 막대 + 추세 변화율 |

### 9.1.4 RFO Detail 전면 재설계

| 항목 | 변경 내용 |
|------|----------|
| RFO 선택 | 검색 가능 콤보박스 (키워드 필터링, 2줄 항목, stopPropagation) |
| Summary Strip | Aircraft/Type/Start/Target Out/Days Remaining/Priority (flex-wrap) |
| KPI (6개) | Tasks, Planned MH, Actual MH, MH Variance, OT Hours, Blockers |
| MH Burndown | 주간 스택 차트 + 수치, 인라인 색상 (#2e5a8a/#c8850a/#dee4ed), 범례 통일 |
| Efficiency Metrics | Productive Ratio, OT Ratio, First-Time Completion, Avg Cycle Time |
| Active Blockers | 차단 요인 카드 (경과 일수, 태스크, 사유) |
| Worker Allocation | 워커별 MH/태스크 분포 + 미배정 경고 |

### 9.1.5 기타 화면 변경

| 화면 | 변경 |
|------|------|
| OT Request | Technician Roster에 검색 입력 + 팀/Shop 컨텍스트 |
| Settings | "Snapshot Week Configuration": Advance On (요일+시간) → 주간 범위 자동 연동, 커스텀 ‹/› 네비게이터 |
| Personnel | Add User 모달 (Employee No./Name/Team/Role/Email), Edit User 모달 (읽기전용 No. + 편집 + Active/Deactivate) |

### 9.2 기능 매트릭스

| 기능 | Task Manager (ADMIN) | Data Entry (SUPERVISOR) |
|------|---------------------|------------------------|
| Import RFO (Excel/CSV) | ✅ | ❌ |
| Create & Assign Task | ✅ | ❌ |
| Add Task (현재 AC 하위) | ❌ | ✅ (EDIT+) |
| Bulk Assign (다건 배포) | ✅ | ❌ |
| Table/Kanban/RFO 3뷰 | ✅ | ❌ |
| 상태/MH/remarks 수정 | 읽기전용 | ✅ (EDIT+) |
| Worker 배정 | ❌ | ✅ (EDIT+) |
| 상세 패널 (조회) | ✅ | ✅ (편집) |
| Export / Audit Log | ✅ | ❌ |
| Pagination (10건/페이지) | ✅ | ❌ (AC 단위 스크롤) |
| Init-week (carry-over) | ✅ (MANAGE) | ❌ |
| Soft delete / Restore | ✅ (MANAGE) | ❌ |

### 9.3 HTMX 인터랙션 패턴

| 패턴 | 설명 |
|---|---|
| OT Submit | Form `hx-post="/api/ot"` → partial HTML with success/error → `hx-swap="innerHTML"` |
| OT List Filter | `hx-get="/views/ot/list?status=PENDING"` → `hx-target="#ot-table"` → table body 교체 |
| OT Endorse (SUP) | `hx-post="/api/ot/{id}/endorse"` + `hx-vals='{"action":"APPROVE"}'` → status ENDORSED 반영 partial |
| OT Approve (ADMIN) | `hx-post="/api/ot/{id}/approve"` + `hx-vals='{"action":"APPROVE"}'` → status APPROVED 반영 partial |
| Task Init-week | `hx-post="/api/tasks/init-week"` → result count 표시 (중복 클릭 안전 — 결과 count 표시) |
| Task Import Preview | multipart form `hx-post="/api/tasks/import"` → preview modal/body swap |
| Task Import Confirm | `hx-post="/api/tasks/import/confirm"` → 생성 결과 toast + 목록 refresh |
| Task Assign / Bulk Assign | `hx-post="/api/tasks/{id}/assign"` 또는 `hx-post="/api/tasks/bulk-assign"` → Assigned badge/Distribution 섹션 갱신 |
| Task Assign Worker | `hx-patch="/api/tasks/{id}/assign-worker"` → Worker badge/quick update partial refresh |
| Task Batch Save | JS로 변경사항 수집 → `hx-patch="/api/tasks/snapshots/batch"` → 성공/실패 표시 |
| RFO Detail Selector | `hx-get` + query param 변경 → Summary/KPI/Blockers/Allocation 영역 partial swap |
| Pagination | `hx-get` with page param → table container swap |

### 9.4 모바일 최적화

#### 반응형 브레이크포인트

| 브레이크포인트 | 대상 |
|--------------|------|
| `< 768px` (모바일) | 1열 스택, 패널/모달 100% 폭 |
| `768px ~ 1023px` (태블릿) | 기본 반응형, 사이드바 숨김 |
| `≥ 1024px` (데스크탑) | 전체 레이아웃 |

#### 모바일 대응 상세

| 요소 | 모바일 동작 |
|------|-----------|
| Data Entry 좌측 패널 | `w-full` + `max-height: 35vh`, 하단 스택 |
| 상세 패널 (split-detail) | `position: fixed` + `w-full` |
| 칸반 디테일 패널 | `w-full` (420px → 100%) |
| 모든 모달 | `w-full` + 16px 좌우 패딩 |
| RFO Summary Strip | `flex-wrap`, 세로 구분선 숨김 |
| 하단바 (pagination) | `flex-col` 세로 스택 |
| 필터바 | `flex-wrap`, 각 필터 `flex: 1 1 45%` |
| 그리드 (3~6열) | 모바일 1~2열 → sm 2~3열 → lg 전체 |
| RFO 콤보박스 | `min-width: 0` (380px 제거) |
| Data Entry flex 방향 | `flex-col sm:flex-row` |

#### Tailwind 반응형 패턴

```
grid-cols-5   → grid-cols-2 sm:grid-cols-3 lg:grid-cols-5
grid-cols-6   → grid-cols-2 sm:grid-cols-3 lg:grid-cols-6
grid-cols-4   → grid-cols-2 sm:grid-cols-4
grid-cols-3   → grid-cols-1 sm:grid-cols-3
grid-cols-2   → grid-cols-1 sm:grid-cols-2
w-[420px]     → w-full sm:w-[420px]
w-[380px]     → w-full sm:w-[380px]
w-[320px]     → w-full sm:w-[320px]
min-w-[380px] → min-w-0 sm:min-w-[380px]
```

### 9.5 현장 UX 필수 규칙
- **모바일 우선**: 하단 고정 버튼, 큰 터치 영역 (최소 44×44px)
- **실패 시 에러 메시지**: 명확한 사유 표시 (redirect만 하지 말 것)
- **저장 실패 시 입력값 유지 + 재시도 가능** (데이터 유실 방지)
- **init-week**: 처리 결과 count 표시 ("5건 생성, 2건 skip")
- **Version 충돌 시**: "다른 사용자가 수정했습니다. 새로고침 후 다시 시도하세요" 메시지
- **MH 감소 차단 시**: "MH는 누적 값이므로 감소할 수 없습니다. 관리자에게 문의하세요" 메시지
- **Import Preview**: 오류 행(row)별 원인을 확인한 뒤 confirm할 수 있어야 함
- **배포 상태 표시**: NEW / NEEDS UPDATE / Unassigned 배지가 목록에서 즉시 식별 가능해야 함

---

## 10. Audit Logs (공통)

모든 write 작업은 audit_logs에 기록한다.

- **entity_type**: ot_request, ot_approval, task_item, task_snapshot, shop, user_shop_access, user, system_config
- **action**: CREATE, UPDATE, DELETE, RESTORE, DEACTIVATE, REACTIVATE
- **before_json / after_json**: 변경 전후 전체 행 JSON 문자열(NVARCHAR(MAX)) (CREATE 시 before=null, DELETE 시 after=null)
- MH 감소 시: after_json에 `correction_reason` 포함 → 감사 추적 가능
- 배치 업데이트(`/api/tasks/snapshots/batch`) 시: batch 내 각 snapshot 변경에 대해 **snapshot별 1개 audit_logs 행** 생성 (예: 3건 수정 → audit_logs 3행)

```python
# Audit log contract
{
    "actor_id": <user performing the action>,
    "entity_type": "task_snapshot",
    "entity_id": 42,
    "action": "UPDATE",
    "before_json": {"mh_incurred_hours": 10.0, ...},
    "after_json": {"mh_incurred_hours": 8.0, "correction_reason": "Initial estimate was overstated", ...},
    "created_at": "2026-02-26T10:30:00+08:00"
}
```

---

## 11. Reporting (SQL Server Views → Power BI)

> DB가 MSSQL로 전환됨에 따라, 리포팅 View도 SQL Server(T‑SQL) 문법으로 제공한다.  
> Power BI는 MSSQL View를 직접 연결하여 fact/dim 스타 스키마로 사용한다.

### 11.1 OT Views (Base A 복원)

```sql
CREATE OR ALTER VIEW dbo.vw_fact_ot_requests AS
SELECT
    otr.id                  AS ot_request_id,
    otr.user_id,
    u.employee_no,
    u.name                  AS employee_name,
    u.team,
    otr.submitted_by,
    sb.name                 AS submitted_by_name,
    sb.employee_no          AS submitted_by_employee_no,
    otr.[date]              AS ot_date,
    otr.start_time,
    otr.end_time,
    otr.requested_minutes,
    otr.reason_code,
    otr.status,
    otr.work_package_id,
    otr.shop_stream_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ac.ac_reg,
    ac.airline,
    ss.shop_code,

    -- 1차 (SUPERVISOR)
    ota1.approver_id        AS endorser_id,
    eu.name                 AS endorser_name,
    ota1.action             AS endorse_action,
    ota1.comment            AS endorse_comment,
    ota1.acted_at           AS endorsed_at,

    -- 2차 (ADMIN)
    ota2.approver_id        AS final_approver_id,
    au.name                 AS final_approver_name,
    ota2.action             AS approval_action,
    ota2.comment            AS approval_comment,
    ota2.acted_at           AS approved_at,

    -- timestamps
    otr.created_at          AS submitted_at,

    -- turnaround (전체: 제출 → 최종 승인)
    CAST(DATEDIFF(SECOND, otr.created_at, ota2.acted_at) AS FLOAT) / 3600.0
                            AS turnaround_hours,

    -- turnaround (1차: 제출 → endorse)
    CAST(DATEDIFF(SECOND, otr.created_at, ota1.acted_at) AS FLOAT) / 3600.0
                            AS endorse_turnaround_hours

FROM dbo.ot_requests otr
JOIN dbo.users u ON u.id = otr.user_id
LEFT JOIN dbo.users sb ON sb.id = otr.submitted_by

LEFT JOIN dbo.ot_approvals ota1 ON ota1.ot_request_id = otr.id AND ota1.stage = 'ENDORSE'
LEFT JOIN dbo.ot_approvals ota2 ON ota2.ot_request_id = otr.id AND ota2.stage = 'APPROVE'
LEFT JOIN dbo.users eu ON eu.id = ota1.approver_id
LEFT JOIN dbo.users au ON au.id = ota2.approver_id

LEFT JOIN dbo.work_packages wp ON wp.id = otr.work_package_id
LEFT JOIN dbo.aircraft ac ON ac.id = wp.aircraft_id
LEFT JOIN dbo.shop_streams ss ON ss.id = otr.shop_stream_id;
```

### 11.2 Task Views (신규)

```sql
CREATE OR ALTER VIEW dbo.vw_fact_task_snapshots AS
SELECT
    ts.id                   AS snapshot_id,
    ts.task_id,
    ts.meeting_date,
    ti.shop_id,
    s.code                  AS shop_code,
    s.name                  AS shop_name,
    ti.aircraft_id,
    ti.work_package_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ti.assigned_supervisor_id,
    sup.name                AS assigned_supervisor_name,
    ti.assigned_worker_id,
    wu.name                 AS assigned_worker_name,
    ti.distributed_at,
    ti.planned_mh,
    ac.ac_reg,
    ac.airline,
    ti.task_text,
    ts.status,
    ts.mh_incurred_hours,
    CASE
        WHEN ti.planned_mh IS NULL THEN NULL
        ELSE CAST(ts.mh_incurred_hours AS DECIMAL(10,2)) - CAST(ti.planned_mh AS DECIMAL(10,2))
    END AS mh_variance,
    -- 주간 투입량 (누적 차이)
    LAG(ts.mh_incurred_hours) OVER (
        PARTITION BY ts.task_id ORDER BY ts.meeting_date
    ) AS prev_mh_incurred_hours,
    ts.mh_incurred_hours - COALESCE(
        LAG(ts.mh_incurred_hours) OVER (
            PARTITION BY ts.task_id ORDER BY ts.meeting_date
        ), 0
    ) AS weekly_mh_delta,
    ts.remarks,
    ts.critical_issue,
    ts.has_issue,
    ts.deadline_date,
    ts.correction_reason,
    ts.supervisor_updated_at,
    ts.last_updated_at,
    ts.last_updated_by,
    lu.name                 AS last_updated_by_name,
    ti.is_active            AS task_is_active
FROM dbo.task_snapshots ts
JOIN dbo.task_items ti ON ti.id = ts.task_id
JOIN dbo.shops s ON s.id = ti.shop_id
JOIN dbo.aircraft ac ON ac.id = ti.aircraft_id
LEFT JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
LEFT JOIN dbo.users sup ON sup.id = ti.assigned_supervisor_id
LEFT JOIN dbo.users wu ON wu.id = ti.assigned_worker_id
LEFT JOIN dbo.users lu ON lu.id = ts.last_updated_by
WHERE ts.is_deleted = 0;                               -- 삭제된 스냅샷 기본 제외

-- 삭제 포함 전체 (관리/감사 분석용)
CREATE OR ALTER VIEW dbo.vw_fact_task_snapshots_all AS
SELECT
    ts.id                   AS snapshot_id,
    ts.task_id,
    ts.meeting_date,
    ti.shop_id,
    s.code                  AS shop_code,
    s.name                  AS shop_name,
    ti.aircraft_id,
    ti.work_package_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ti.assigned_supervisor_id,
    sup.name                AS assigned_supervisor_name,
    ti.assigned_worker_id,
    wu.name                 AS assigned_worker_name,
    ti.distributed_at,
    ti.planned_mh,
    ac.ac_reg,
    ac.airline,
    ti.task_text,
    ts.status,
    ts.mh_incurred_hours,
    CASE
        WHEN ti.planned_mh IS NULL THEN NULL
        ELSE CAST(ts.mh_incurred_hours AS DECIMAL(10,2)) - CAST(ti.planned_mh AS DECIMAL(10,2))
    END AS mh_variance,
    LAG(ts.mh_incurred_hours) OVER (
        PARTITION BY ts.task_id ORDER BY ts.meeting_date
    ) AS prev_mh_incurred_hours,
    ts.mh_incurred_hours - COALESCE(
        LAG(ts.mh_incurred_hours) OVER (
            PARTITION BY ts.task_id ORDER BY ts.meeting_date
        ), 0
    ) AS weekly_mh_delta,
    ts.remarks,
    ts.critical_issue,
    ts.has_issue,
    ts.deadline_date,
    ts.correction_reason,
    ts.supervisor_updated_at,
    ts.is_deleted,
    ts.deleted_at,
    ts.deleted_by,
    ts.last_updated_at,
    ts.last_updated_by,
    lu.name                 AS last_updated_by_name,
    ti.is_active            AS task_is_active
FROM dbo.task_snapshots ts
JOIN dbo.task_items ti ON ti.id = ts.task_id
JOIN dbo.shops s ON s.id = ti.shop_id
JOIN dbo.aircraft ac ON ac.id = ti.aircraft_id
LEFT JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
LEFT JOIN dbo.users sup ON sup.id = ti.assigned_supervisor_id
LEFT JOIN dbo.users wu ON wu.id = ti.assigned_worker_id
LEFT JOIN dbo.users lu ON lu.id = ts.last_updated_by;
```

### 11.3 Dimension Views

```sql
CREATE OR ALTER VIEW dbo.vw_dim_employee AS
SELECT
    u.id            AS employee_key,
    u.employee_no,
    u.name,
    u.team,
    u.is_active,
    STRING_AGG(r.name, ',') WITHIN GROUP (ORDER BY r.name) AS roles_csv
FROM dbo.users u
JOIN dbo.user_roles ur ON ur.user_id = u.id
JOIN dbo.roles r ON r.id = ur.role_id
GROUP BY u.id, u.employee_no, u.name, u.team, u.is_active;

CREATE OR ALTER VIEW dbo.vw_dim_aircraft AS
SELECT id AS aircraft_key, ac_reg, airline, status
FROM dbo.aircraft;

CREATE OR ALTER VIEW dbo.vw_dim_work_package AS
SELECT wp.id AS work_package_key, wp.aircraft_id, ac.ac_reg, wp.rfo_no, wp.title,
       wp.start_date, wp.end_date, wp.priority, wp.status
FROM dbo.work_packages wp
JOIN dbo.aircraft ac ON ac.id = wp.aircraft_id;

CREATE OR ALTER VIEW dbo.vw_dim_shop_stream AS
SELECT ss.id AS shop_stream_key, ss.work_package_id, wp.title AS work_package_title,
       ss.shop_code, ss.status
FROM dbo.shop_streams ss
JOIN dbo.work_packages wp ON wp.id = ss.work_package_id;

CREATE OR ALTER VIEW dbo.vw_dim_shop AS
SELECT id AS shop_key, code, name
FROM dbo.shops;

-- Enum dimension (MSSQL: Postgres enum iteration 대체)
CREATE OR ALTER VIEW dbo.vw_dim_task_status AS
SELECT 'NOT_STARTED' AS status
UNION ALL SELECT 'IN_PROGRESS'
UNION ALL SELECT 'WAITING'
UNION ALL SELECT 'COMPLETED';

CREATE OR ALTER VIEW dbo.vw_dim_ot_reason AS
SELECT 'BACKLOG' AS reason_code
UNION ALL SELECT 'AOG'
UNION ALL SELECT 'SCHEDULE_PRESSURE'
UNION ALL SELECT 'MANPOWER_SHORTAGE'
UNION ALL SELECT 'OTHER';

-- Date dimension (2026-01-01 ~ 2027-12-31)
CREATE OR ALTER VIEW dbo.vw_dim_date AS
WITH nums AS (
    SELECT TOP (730)
        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects a
    CROSS JOIN sys.all_objects b
),
dates AS (
    SELECT DATEADD(DAY, n, CAST('2026-01-01' AS DATE)) AS date_key
    FROM nums
)
SELECT
    date_key,
    DATEPART(YEAR, date_key)      AS [year],
    DATEPART(MONTH, date_key)     AS [month],
    DATEPART(DAY, date_key)       AS [day],
    DATENAME(WEEKDAY, date_key)   AS day_name,
    DATEPART(WEEKDAY, date_key)   AS day_of_week,
    DATEPART(ISO_WEEK, date_key)  AS week_number,
    DATENAME(MONTH, date_key)     AS month_name,
    DATEPART(QUARTER, date_key)   AS quarter
FROM dates;
```

### 11.4 MiniPatch 12 추가 Views

| View | 최소 출력 계약 / 용도 |
|------|------------------------|
| `vw_fact_ot_by_reason` | `month`, `team`, `reason_code`, `hours`, `pct` — Reason Code별 OT 시간 집계 |
| `vw_fact_ot_weekly` | `month`, `week_number`, `label`, `hours` — 주간별 OT 추세 |
| `vw_rfo_efficiency` | `work_package_id`, `rfo_no`, `planned_mh`, `actual_mh`, `mh_variance`, `productive_ratio`, `ot_ratio`, `ftc_pct`, `avg_cycle_time_weeks`, `blocker_count` |
| `vw_rfo_burndown` | `work_package_id`, `rfo_no`, `week`, `cumulative_mh`, `remaining_mh` — RFO 주간 Burndown |
| `vw_task_distribution` | `work_package_id`, `rfo_no`, `total`, `assigned_sup`, `assigned_worker`, `unassigned`, `updated_count` — 배포/배정 현황 |

> 위 5개 view는 `scripts/create_views.py` 및 운영 migration에 포함한다. 계산식의 정의는 §7.4를 따른다.

### 11.5 리포트 정의


- **"완료"**: 해당 meeting_date의 snapshot status = COMPLETED
- **task_item 수준 최종 완료**: 최신 meeting_date의 snapshot이 COMPLETED
- **주간 투입량**: `weekly_mh_delta` 컬럼 (view에 선반영) 또는 DAX에서 계산
- **키 리스크 방지**: 모든 fact는 IDENTITY PK(non-null). dim key도 non-null. Blank key 금지


---

## 12. Validation Rules 요약

| Entity | Field | Rule |
|---|---|---|
| ot_requests | date | ≥ today |
| ot_requests | start_time | < end_time (CHECK 제약) |
| ot_requests | requested_minutes | > 0 (CHECK 제약); = end_time − start_time |
| ot_requests | reason_code | valid enum |
| ot_requests | shop_stream_id | 제공 시 해당 WP 소속 |
| ot_requests | 중복 | same user + date + 시간 겹침 → DUPLICATE_OT |
| ot_approvals | self | approver_id ≠ ot_request.user_id |
| ot_approvals | team | supervisor.team = worker.team (unless ADMIN) |
| ot_approvals | status | PENDING만 승인/반려 가능 |
| task_items | aircraft_id | NOT NULL. 유효한 aircraft |
| task_items | shop_id | NOT NULL. 유효한 shop |
| task_items | task_text | NOT NULL, non-empty |
| task_snapshots | status | valid task_status enum |
| task_snapshots | mh_incurred_hours | ≥ 0. EDIT: ≥ 이전 주. MANAGE: 감소 시 correction_reason 필수 |
| task_snapshots | version | PATCH 시 필수. 불일치 → CONFLICT_VERSION |
| task_snapshots | correction_reason | MH 감소 + MANAGE 시 필수. 그 외 NULL 허용 |

---

## 13. 테스트(Testing)

### 13.1 테스트 Fixtures

```python
@pytest.fixture
async def db_session():
    """Transactional test session that rolls back after each test."""

@pytest.fixture
async def worker_client(db_session):
    """Authenticated httpx client as WORKER (TEAM-A)."""

@pytest.fixture
async def supervisor_client(db_session):
    """Authenticated httpx client as SUPERVISOR (TEAM-A)."""

@pytest.fixture
async def admin_client(db_session):
    """Authenticated httpx client as ADMIN."""

@pytest.fixture
async def edit_user_client(db_session):
    """Authenticated client with EDIT access to test shop."""

@pytest.fixture
async def manage_user_client(db_session):
    """Authenticated client with MANAGE access to test shop."""
```

**테스트 환경 인증 우회:**

Azure AD 로그인을 테스트에서 직접 호출할 수 없으므로, 두 가지 방식 중 택 1.

**방식 A: Dependency Override (추천)**

```python
# conftest.py
from app.api.deps import get_current_user

def make_test_user(user_id, roles, team=None):
    """테스트용 사용자 객체 생성."""
    user = User(
        id=user_id,
        employee_no=f"E{user_id:03d}",
        name=f"Test User {user_id}",
        team=team,
        is_active=True,
    )
    user._roles = roles  # 또는 별도 속성
    return user

@pytest.fixture
async def worker_client(db_session):
    test_user = make_test_user(10, ["WORKER"], "TEAM-A")
    app.dependency_overrides[get_current_user] = lambda: test_user
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
```

**방식 B: 세션 직접 주입**

```python
@pytest.fixture
async def worker_client(db_session):
    async with AsyncClient(app=app, base_url="http://test") as client:
        # 테스트용 세션 쿠키 직접 설정
        session_data = encode_session({"user_id": 10, "roles": ["WORKER"], "team": "TEAM-A"})
        client.cookies.set("session", session_data)
        # CSRF도 테스트용 고정값
        client.cookies.set("csrftoken", "test-csrf")
        client.headers["X-CSRFToken"] = "test-csrf"
        yield client
```

두 방식 모두 Azure AD를 거치지 않고 인증된 상태를 시뮬레이션한다.  
방식 A가 더 깔끔하고, 필요하다면 테스트 환경에서 CSRF 미들웨어를 disable 할 수도 있다.


### 13.2 OT 필수 시나리오

1. Worker submits OT → **PENDING** + audit_logs
2. Supervisor endorses approve → **ENDORSED** + approval row(`stage=ENDORSE`, `action=APPROVE`)
2a. Supervisor rejects → **REJECTED** + approval row(`stage=ENDORSE`, `action=REJECT`)
3. Admin approves endorsed → **APPROVED** + approval row(`stage=APPROVE`, `action=APPROVE`)
3a. Admin rejects endorsed → **REJECTED** + approval row(`stage=APPROVE`, `action=REJECT`)
4. Worker cancels pending → **CANCELLED**
5. Wrong team endorse → **403 OT_WRONG_TEAM**
6. Self endorse/approve → **403 SELF_ENDORSE**
7. Admin tries approve pending → **409 INVALID_STATUS**
8. Worker tries cancel endorsed → **409 INVALID_STATUS**
9. Duplicate OT (overlapping time) → **422 DUPLICATE_OT**
10. Monthly limit exceeded on single submit → **422 OT_MONTHLY_LIMIT_EXCEEDED**
11. Monthly limit exceeded on bulk submit → 해당 user **skip reason=MONTHLY_LIMIT_EXCEEDED**
12. CANCELLED OT는 monthly sum에서 제외
13. Past date OT → **422 VALIDATION_ERROR**


### 13.3 Task 필수 시나리오

1. init-week 최초 → 미완료+활성 task carry-over 생성 (created_count > 0)
2. init-week 재호출 → idempotent (created_count=0, skipped_count > 0)
3. COMPLETED → carry-over 제외
4. is_deleted snapshot → carry-over 제외 + 기본 조회 제외
5. **is_active=false task → carry-over 제외**
6. **carry-over 시 mh_incurred_hours 이전 주 값 복사 확인**
7. deadline set → clear(null) → 재set 가능
8. soft delete → restore → 기본 목록 재노출
9. 모든 write → audit_logs 기록

### 13.4 동시성/보안 필수 시나리오

10. **동시 PATCH 같은 snapshot → 후발 409 CONFLICT_VERSION**
11. **배치: 1건 version 충돌 → 전체 롤백**
12. **배치: 1건 field 오류 → 전체 롤백**
13. 미인증 요청 → 401 AUTH_REQUIRED
14. 타 shop 접근 (access 없음) → 403 SHOP_ACCESS_DENIED
15. **ADMIN + user_shop_access 행 없음 → 전체 shop 접근 가능**
16. VIEW 사용자 → 스냅샷 수정 시도 → 403
17. EDIT 사용자 → soft delete 시도 → 403
18. EDIT 사용자 → init-week 시도 → 403
19. **CSRF 토큰 없는 POST → 403 CSRF_INVALID**

### 13.5 MH 누적/감소 시나리오

20. **EDIT 권한 + mh 감소 → 422 MH_DECREASE_FORBIDDEN**
21. **MANAGE 권한 + mh 감소 + correction_reason 누락 → 422 CORRECTION_REASON_REQUIRED**
22. **MANAGE 권한 + mh 감소 + correction_reason 입력 → 성공 + audit에 correction_reason 포함**
23. **첫 주 (이전 snapshot 없음) → mh 제한 없음**

---

### 13.6 MiniPatch 12 추가 시나리오

1. **Task Import Preview**: 유효/오류 row 분리, `valid_count`/`error_count` 정확성 확인
2. **Task Import Confirm**: all-or-nothing 저장 + audit_logs 기록 확인
3. **Assign / Bulk Assign**: `assigned_supervisor_id`, `distributed_at` 설정 및 권한 검증
4. **Assign Worker**: 본인 Shop 내 Worker만 허용, 타 Shop Worker 지정 시 403
5. **NEW 배지**: `distributed_at` 존재 + `supervisor_updated_at = NULL` 인 Task만 NEW로 표시
6. **RFO 배포/업데이트 배지**: assigned / updated 카운트가 RFO 뷰와 일치하는지 확인
7. **RFO Metrics API**: `/api/rfo/{id}/metrics`, `/blockers`, `/worker-allocation`, `/burndown` 응답 구조 검증
8. **OT Stats 확장**: `/api/stats/ot-by-reason`, `/api/stats/ot-weekly-trend` 권한/집계 검증

## 14. Seed Data (Development)

```python
ROLES = ["WORKER", "SUPERVISOR", "ADMIN"]

USERS = [
    {"employee_no": "E001", "name": "Admin User",      "team": "ADMIN",  "roles": ["ADMIN"]},
    {"employee_no": "E002", "name": "Supervisor Lee",   "team": "TEAM-A", "roles": ["SUPERVISOR"]},
    {"employee_no": "E003", "name": "Supervisor Tan",   "team": "TEAM-B", "roles": ["SUPERVISOR"]},
    {"employee_no": "E010", "name": "Worker Ali",       "team": "TEAM-A", "roles": ["WORKER"]},
    {"employee_no": "E011", "name": "Worker Muthu",     "team": "TEAM-A", "roles": ["WORKER"]},
    {"employee_no": "E012", "name": "Worker Ahmad",     "team": "TEAM-A", "roles": ["WORKER"]},
    {"employee_no": "E020", "name": "Worker Siti",      "team": "TEAM-B", "roles": ["WORKER"]},
    {"employee_no": "E021", "name": "Worker Kumar",     "team": "TEAM-B", "roles": ["WORKER"]},
]

# airline 필드: 운영에서는 풀네임 또는 코드 혼용 가능 (§7.2.8 분류 로직이 양쪽 매칭).
# Seed에서는 풀네임 사용. UI 프로토타입의 airline 태그(SIA, Scoot)는 display 전용.
AIRCRAFT = [
    {"ac_reg": "9V-SMA", "airline": "Singapore Airlines"},
    {"ac_reg": "9V-SMB", "airline": "Singapore Airlines"},
    {"ac_reg": "9M-MRA", "airline": "Malaysia Airlines"},  # Third Parties
]

WORK_PACKAGES = [
    {"aircraft": "9V-SMA", "rfo_no": "1200000101", "title": "C-Check 2026-Q1", "priority": 1},
    {"aircraft": "9V-SMB", "rfo_no": "1200000102", "title": "A-Check Feb 2026", "priority": 2},
    {"aircraft": "9M-MRA", "rfo_no": "1200000201", "title": "Engine Change #3", "priority": 1},
]

SHOP_STREAMS = [
    {"work_package": "C-Check 2026-Q1", "shop_code": "STRUCT"},
    {"work_package": "C-Check 2026-Q1", "shop_code": "AVIONICS"},
    {"work_package": "C-Check 2026-Q1", "shop_code": "CABIN"},
    {"work_package": "A-Check Feb 2026", "shop_code": "GEN-MECH"},
    {"work_package": "Engine Change #3", "shop_code": "ENGINE"},
]

SHOPS = [
    {"code": "SHEET_METAL", "name": "Sheet Metal"},
    {"code": "FIBERGLASS", "name": "Fiberglass"},
    {"code": "FABRIC", "name": "Fabric"},
    {"code": "PAINTING", "name": "Painting"},
]

# user_shop_access: E002(Supervisor Lee) → SHEET_METAL(MANAGE), E010(Worker Ali) → SHEET_METAL(EDIT)


SYSTEM_CONFIG = [
    ("meeting_current_date",       "2026-02-26"),
    ("meeting_auto_advance",       "every_monday"),
    ("teams_enabled",              "true"),
    ("teams_recipients",           "#cis-sheet-metal"),
    ("teams_message_template",     "Weekly Summary — {shop} · Week {week}: {task_count} tasks, {issues} issues flagged."),
    ("outlook_enabled",            "false"),
    ("outlook_recipients",         ""),
    ("outlook_subject_template",   "[CIS ERP] OT Approval Reminder — {date}"),
    ("outlook_body_template",      "You have {pending_count} pending OT requests awaiting approval for {shop}. Please review at your earliest convenience."),
    ("critical_alert_enabled",     "true"),
    ("critical_alert_recipients",  "#cis-alerts"),
]
```

---

## 15. 운영/배포

### 15.1 개발 환경 (Docker Compose)

- DB는 기본적으로 **사내 MSSQL 인스턴스(또는 Azure SQL)** 를 사용한다. (컨테이너 외부)
- 앱은 Docker container로 패키징하여 실행한다. (MiniPatch 9)

```yaml
version: "3.9"
services:
  app:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      # MSSQL (ODBC Driver 18)
      DATABASE_URL: "mssql+aioodbc://mh_app:dev_password@mssql-host:1433/mh_tracking?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
      SECRET_KEY: dev-secret-key-change-in-prod
      ENV: development

      # Azure AD OAuth2 (Authorization Code Flow)
      AZURE_CLIENT_ID: "replace-me"
      AZURE_CLIENT_SECRET: "replace-me"
      AZURE_TENANT_ID: "replace-me"
      AZURE_REDIRECT_URI: "http://localhost:8000/auth/callback"
      SESSION_EXPIRE_HOURS: "8"
    ports:
      - "8000:8000"
    volumes:
      - .:/app
```

### 15.1.1 Dockerfile (ODBC Driver 18 설치 예시)

```dockerfile
FROM python:3.12-slim
WORKDIR /app

# ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y \
    unixodbc-dev gnupg curl && \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 15.2 환경변수

| Variable | Required | Default | Description |
|---|---|---|---|
| DATABASE_URL | Yes | — | MSSQL connection string (`mssql+aioodbc` 권장) |
| SECRET_KEY | Yes | — | Session signing/encryption key |
| ENV | No | development | development / production |
| CORS_ORIGINS | No | ["*"] | Allowed CORS origins (prod에서 명시적 도메인) |
| SESSION_EXPIRE_HOURS | No | 8 | Session duration (hours) |
| AZURE_CLIENT_ID | Yes | — | Azure App Registration client ID |
| AZURE_CLIENT_SECRET | Yes | — | Azure App Registration client secret |
| AZURE_TENANT_ID | Yes | — | Azure tenant ID |
| AZURE_REDIRECT_URI | Yes | — | e.g. `https://<app-url>/auth/callback` |

### 15.3 마이그레이션 순서

적용 순서 (FK 의존성 기준):
1. users, roles, user_roles
2. aircraft, work_packages, shop_streams
3. ot_requests, ot_approvals
4. audit_logs
5. system_config
6. **shops**
7. **user_shop_access**
8. **task_items**
9. **task_snapshots**
10. Future tables (Phase 2/3 schema only)
11. **Reporting views** (vw_fact_*, vw_dim_*)

롤백은 역순. 각 마이그레이션은 단일 트랜잭션으로 실행.

### 15.4 운영(Prod) 요구사항
- Reverse proxy (HTTPS) + app containers + MSSQL 인스턴스(사내 또는 Azure SQL)
- Secrets: 환경변수 / secret manager로 관리
- Backup/restore: DB 정책에 맞춰 수행 (Azure SQL 사용 시 PITR 권장)
- 로그: 구조화된 JSON 로깅(uvicorn + structlog 등)
- 모니터링: 헬스체크 엔드포인트 `/health`

### 15.5 배포 전략 (우선순위 — MiniPatch 9)

1. **Docker container (사내 서버)**  
   - Dockerfile로 FastAPI + 의존성 패키징  
   - docker-compose로 앱 컨테이너 실행  
   - DB는 기존 사내 MSSQL 인스턴스 사용 (컨테이너 외부)  
   - 추가 비용: 없음  

2. **Azure App Service**  
   - Azure 구독 내 Python App Service  
   - DB: Azure SQL  
   - 추가 비용: 월 $15~70 예상  

3. **Linux VM 직접 설치**  
   - Python 3.11+ 직접 설치  
   - 추가 비용: VM 신규 할당 시 발생 가능  


---

## 16. 오픈 결정(Open Decisions)

| # | Decision | Status |
|---|---|---|
| 1 | ~~mh_incurred_hours: 누적 vs 증분~~ | **Decided: 누적 합산.** §5.3.4, §7.2.7에 반영 |
| 2 | ~~carry-over 복사 정책~~ | **Decided: 모든 필드 복사 (mh 포함).** §7.2.2에 반영 |
| 3 | shops와 shop_streams 매핑/통합 여부 | **Open (Phase 2).** 현재 별도 엔티티 |
| 4 | Power Apps(SharePoint) 데이터 이관 전략 | **Open** |
| 5 | HasIssue 자동 계산 vs 사용자 토글 | **Open (Phase 2).** 현재 사용자 토글 |
| 6 | 알림/스케줄러: Power Automate vs ERP | **Open (Phase 2+)** |

---

## 17. 변경 관리(Change Control)

- SSOT 변경은 PR처럼 관리한다.
- 변경 시 영향 범위(스키마/API/리포트/테스트)를 명시하고 승인 후 반영한다.
- 본 문서(v2.0)에서 해소된 Open Decision은 다시 열지 않는다.

---

## 부록 A. 산출물 체크리스트(개발 인계용)

### DB/Migration
- [ ] Alembic migration: shops + user_shop_access + task_items + task_snapshots (CHECK constraints 포함)
- [ ] 모든 FK에 ON DELETE NO ACTION/CASCADE 적용
- [ ] 인덱스 생성 (idx_snap_meeting_deleted, idx_taskitem_shop, idx_taskitem_aircraft, idx_taskitem_active, idx_snap_task)
- [ ] Reporting views: vw_fact_task_snapshots (+ _all), vw_fact_ot_by_reason, vw_fact_ot_weekly, vw_rfo_efficiency, vw_rfo_burndown, vw_task_distribution, vw_dim_shop, vw_dim_task_status

### API
- [ ] /api/ot (POST submit — self/proxy/bulk)
- [ ] /api/ot/export/csv (GET)
- [ ] /api/tasks/init-week
- [ ] /api/tasks (POST create)
- [ ] /api/tasks/snapshots (GET list)
- [ ] /api/tasks/snapshots/{id} (PATCH update — version 검증)
- [ ] /api/tasks/snapshots/batch (PATCH — all-or-nothing)
- [ ] /api/tasks/snapshots/{id}/delete, /api/tasks/snapshots/{id}/restore
- [ ] /api/tasks/{id}/deactivate, /api/tasks/{id}/reactivate
- [ ] /api/tasks/export/csv (GET)
- [ ] /api/users/{id} (DELETE — 조건부 HARD DELETE)
- [ ] /api/shops, /api/shop-access (Admin CRUD)
- [ ] 전역: CSRF, rate limit, pagination, 인증 강제

### UI
- [ ] /tasks/meeting — init-week + 인라인 편집 + 배치 저장 + soft delete/restore
- [ ] /tasks/entry — 단건 상태/MH/remarks 업데이트
- [ ] Version 충돌 에러 표시
- [ ] MH 감소 차단 메시지

### 테스트
- [ ] OT 시나리오 9개 CI 통과
- [ ] Task 시나리오 9개 CI 통과
- [ ] 동시성/보안 시나리오 10개 CI 통과
- [ ] MH 누적/감소 시나리오 4개 CI 통과

### Audit
- [ ] 모든 Task write → audit_logs 기록
- [ ] MH 감소 시 correction_reason audit에 포함