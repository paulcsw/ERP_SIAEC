# ERP 샘플 데이터 세트 번들

이 번들은 **ERP 전체 기능 시연 + 수동 테스트 + 리포팅 검증**을 동시에 커버하도록 만든 **합성(synthetic) 데이터 세트**입니다.
실제 개인정보/실적 데이터는 포함하지 않았습니다.

## 포함된 데이터 세트

### 1) `01_smoke_ui_clean`
- 목적: **로컬 bring-up / 빠른 smoke test**
- 특징:
  - 작은 볼륨
  - 현재 UI 기준으로 **Task Snapshot 1개/Task**만 포함
  - Task Entry / Mobile Entry 화면에서 중복 행이 적음
- 추천 용도:
  - Docker로 앱 띄운 직후 화면이 정상인지 확인
  - 레퍼런스 / OT / Task CRUD 기본 동작 확인

### 2) `02_full_demo_ui_clean`
- 목적: **실제 시연용 / 운영 화면 검증용**
- 특징:
  - 사용자/Shop/RFO/Task/OT/승인/감사로그를 폭넓게 포함
  - **Task Snapshot 1개/Task** 구성이라 현재 Task Entry / Mobile 화면이 비교적 깔끔함
  - OT는 `PENDING / ENDORSED / APPROVED / REJECTED / CANCELLED` 전부 포함
  - VIEW / EDIT / MANAGE / no-access 사용자 모두 포함
- 추천 용도:
  - 내부 데모
  - 모바일 화면 확인
  - RFO 상세/대시보드/OT 대시보드 시연
  - CSV export / reference import 확인

### 3) `03_full_demo_history_rich`
- 목적: **리포팅 / 추세 / RFO burndown / 주차별 변화 검증용**
- 특징:
  - `02_full_demo_ui_clean`의 부모 데이터를 그대로 유지하면서
  - `task_snapshots`에 여러 `meeting_date` 주차 히스토리를 추가
- 주의:
  - 현재 코드 기준 `Task Entry / Mobile M2`는 `meeting_date`를 엄격히 필터링하지 않는 부분이 있어
    **이 데이터 세트를 그대로 쓰면 Task 화면에 같은 Task가 여러 번 보일 수 있습니다.**
  - 대신 **RFO burndown / cycle time / 추세 검증**에는 가장 적합합니다.

## 폴더 구조

- 각 데이터 세트 폴더 안 `db_csv/`
  - 실제 테이블 단위 CSV
- `02_full_demo_ui_clean/import_ready/`
  - 현재 앱 import 화면에 맞춘 CSV
  - `reference_aircraft_import.csv`
  - `reference_work_packages_import.csv`
  - `reference_shop_streams_import.csv`
  - `tasks_import_*.csv`

## 추천 사용 순서

1. **가장 먼저:** `01_smoke_ui_clean`
2. **시연 직전:** `02_full_demo_ui_clean`
3. **통계/리포트 깊게 볼 때:** `03_full_demo_history_rich`

## 핵심 데모 포인트

- Dev seed 사용자: `user_id=1`
  - 현재 `/dev/login`과 가장 잘 맞음
  - 팀 = `DEV`
- VIEW only worker: `user_id=19`
- EDIT worker: `user_id=20`
- no-access worker: `user_id=18`
- OT 72h 한도 근접 사용자: `user_id=15`
  - 현재 월 누적: **4200분 = 70.0h**
- Blocked RFO: `RFO-260301`
- Healthy RFO: `RFO-260302`
- Empty/near-empty RFO state: `RFO-260309`

## 현재 기준 주요 설정값

- `meeting_current_date`: `2026-03-16`
- `needs_update_threshold_hours`: `72`

## CSV 로딩 순서 (권장)

아래 순서대로 넣으면 FK 충돌을 피하기 쉽습니다.

1. `roles.csv`
2. `users.csv`
3. `user_roles.csv`
4. `shops.csv`
5. `user_shop_access.csv`
6. `system_config.csv`
7. `aircraft.csv`
8. `work_packages.csv`
9. `shop_streams.csv`
10. `task_items.csv`
11. `task_snapshots.csv`
12. `ot_requests.csv`
13. `ot_approvals.csv`
14. `audit_logs.csv`
15. `shift_templates.csv`
16. `shift_assignments.csv`
17. `attendance_events.csv`
18. `daily_assignments.csv`
19. `worklog_blocks.csv`
20. `time_ledger_daily.csv`
21. `ledger_allocations_daily.csv`

## 참고

- 이 데이터는 **합성 데이터**입니다.
- 실제 운영 환경에 넣기 전에는 반드시 별도 테스트 DB에서 먼저 확인하세요.
- `03_full_demo_history_rich`는 리포팅용이며, 현재 UI 기준으로는 일부 화면에 중복 행처럼 보일 수 있습니다.
