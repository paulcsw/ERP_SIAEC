# ERP Operator Feature Guide - Revised Draft

Last reviewed: April 9, 2026  
Alignment basis: ERP Integrated SSOT v2.0 (MiniPatch 1~12b-fix3) and ERP Implementation Plan v2 aligned to that SSOT.

## Purpose

This guide explains the current user-facing ERP features from an operator point of view.
It is organized around the pages and flows that users actually work with, not around APIs or internal modules.

Use this document to understand:

- what each page is for
- who can use it
- what actions are available
- which actions are desktop-first versus mobile-first
- how shop access, working week, and role-based visibility affect the system

## Canonical Alignment Note

This guide is intended to stay aligned with the current ERP SSOT and implementation plan.
If a page behavior in the running UI conflicts with this guide, the SSOT and implementation plan should be treated as the source of truth until the discrepancy is resolved.

## User Roles And Task Access Model

The ERP uses **two different permission layers**, and they should not be mixed together.

### User Roles

These roles determine a user's primary scope in the system.

- `ADMIN`: full system administration, global task access, final OT approval, settings, reference data, shops, shop access, and user management
- `SUPERVISOR`: team-level operational management, OT first-stage endorsement, and task work within granted shop scope
- `WORKER`: self-service OT and task visibility or update capability only where shop access has been granted

### Task Shop Access Levels

These access levels apply **only to task surfaces** and are granted per shop.
There is **no standalone `VIEW` user role**.

- `VIEW`: read-only task visibility for the granted shop
- `EDIT`: task update capability for the granted shop
- `MANAGE`: broader task control for the granted shop, including management actions such as carry-over or lifecycle actions where enabled

### Cross-Cutting Rules

Two rules affect many task-related pages.

- `Shop Access` controls whether a non-admin user can enter task surfaces for a given shop
- `meeting_current_date` is the effective working week used by Task Manager, Data Entry, and task-related exports

## Navigation Model

### Desktop Navigation

Desktop navigation uses the main sidebar and is the primary experience for broader operational and administrative pages.
This is the normal entry point for:

- Dashboard
- Task Manager
- OT List, OT Approve, and OT Dashboard
- RFO Detail
- Admin pages such as Personnel, Reference, Shops, Shop Access, and Settings

### Mobile Navigation

Mobile navigation uses a bottom tab shell with three tabs:

- `Tasks`
- `OT`
- `More`

The mobile shell is intended for fast day-to-day operational work rather than full administration.
In the current design, workers, supervisors, and admins can all use the shell, but mobile visibility still depends on role and shop access.

### Mobile Role Notes

- A worker with **no task shop access** should see OT and More, but not an active task tab
- A worker with `VIEW` task access can open task pages in read-only mode
- A worker with `EDIT` task access can use mobile quick updates where allowed
- Supervisors can use mobile Tasks, OT, and More, including the OT approval segment
- Admins can use the mobile shell, but Task Manager, Personnel, Reference, Settings, OT Dashboard, and full RFO Detail remain desktop-first surfaces

## Dashboard

### Purpose

The Dashboard is the general operational landing page.
It gives users a quick view of current work status, OT pipeline health, and RFO progress.

### Main Uses

- Review current operational KPIs
- Check the OT approval pipeline
- Review monthly OT usage signals
- Monitor active RFO progress
- Drill into OT and RFO pages from summary widgets

### Key Functions

- KPI summary cards
- OT approval and OT usage snapshot
- RFO progress widget
- Quick links into OT and RFO pages

### Access

- Available to logged-in users
- Scope depends on role and effective team or shop visibility

### Notes

- Dashboard content should emphasize **OT quota, approval pipeline, and RFO progress**, not a generic recent-activity concept
- OT-specific analytics still live on the dedicated OT Dashboard

## Tasks

The Tasks area includes `Task Manager`, `Data Entry`, `Task Detail`, and the mobile task flow.

### Shared Task Rules

- Non-admin users need explicit `Shop Access` to enter task surfaces
- `ADMIN` always bypasses task-surface shop access checks
- Search visibility can be broader than editability; a user may be able to inspect a task without being allowed to update it
- The active working week is controlled by `meeting_current_date`
- Task status, MH, issue flags, and deadline behavior all follow the task snapshot rules defined in the current SSOT

### Task Manager

#### Purpose

Task Manager is the **planning, distribution, and audit hub** for the active working week.
It is primarily an admin surface, with supervisors using it mainly as a scoped read view.

#### Main Uses

- Import tasks from RFO or create them directly
- Assign tasks to supervisors
- Review weekly task distribution and execution state
- Inspect work by table, kanban, or RFO-grouped view
- Audit remarks, issues, MH history, and recent changes

#### Key Functions

- Primary actions: `Import RFO`, `Create & Assign`, and `Assign To…`
- Secondary management action: `Init Week`, where enabled
- Filters for working week, shop, airline, RFO, supervisor, and status
- Search across task text, aircraft registration, and RFO context
- Table, Kanban, and RFO grouped views
- Detail overlay with Distribution, Remarks, Active Issue, MH History, and Audit Trail
- Pagination and export or audit actions where permitted

#### Permissions

- `ADMIN`: primary full-use audience
- `SUPERVISOR`: scoped read-oriented access within granted shop visibility
- `WORKER`: not a Task Manager audience

#### Notes

- Task Manager should be documented as an **admin planning and audit surface**, not as the main day-to-day update screen
- If `Init Week` is available in the deployment, it should still be described even if it is presented as a secondary or overflow action

### Data Entry

#### Purpose

Data Entry is the main task update workstation for supervisors and the main mobile task surface for users who have task access.

#### Main Uses

- Review tasks within the current aircraft context
- Update status, MH, due date, remarks, and issue fields
- Assign a worker to a task where permitted
- Add a task inside the selected aircraft and RFO context
- Use desktop inline editing or the mobile step flow

#### Key Functions

- Aircraft-centered browsing
- Task list for the selected aircraft
- Quick Update controls for status and MH
- Worker assignment controls where allowed
- Add Task flow within the current task context
- Read-only versus editable behavior based on task access level
- `NEW`, `NEEDS UPDATE`, and up-to-date indicators

#### Permissions

- Entry into Data Entry requires task-surface shop access
- `VIEW`: read-only access only
- `EDIT`: quick update and day-to-day editing where permitted
- `MANAGE`: broader control for the shop where enabled
- `ADMIN`: full access

#### Update-State Meanings

- `NEW`: the task has been distributed but not yet updated by the supervisor workflow
- `NEEDS UPDATE`: the last supervisor-side update is older than the configured threshold
- `up to date`: neither NEW nor overdue for update

#### Mobile Task Flow

- `m1`: aircraft list
- `m2`: task list for the selected aircraft
- `m3`: quick update for editable tasks
- `m4`: add task
- `m5`: read-only task detail

#### Notes

- Mobile and desktop follow the same task visibility rules
- `m5` is a read-only detail stage that shows full remarks, issue context, assignment information, snapshot history, and recent audit entries
- On mobile, worker users with `VIEW` access should not see Save or Add actions

### Task Detail

#### Purpose

Task Detail is the full read-focused page for a single task.

#### Main Uses

- Review task metadata and current state
- Inspect snapshot history across weeks
- Review assignment and distribution context
- Check audit trail and update history

#### Key Functions

- Full task detail page
- Snapshot history
- Distribution context
- Audit trail
- Read-focused task inspection without requiring entry into an edit form

#### Permissions

- Visible according to task detail visibility rules
- Unauthorized access should return an access-denied page state rather than raw JSON

## OT

The OT area includes submit, history, detail, approval, dashboard, and mobile OT flow surfaces.

### OT Submit

#### Purpose

Used to create new overtime requests.

#### Main Uses

- Workers submit their own requests
- Supervisors or admins submit proxy requests where permitted

#### Key Functions

- OT request form with worker, date, time, reason, justification, and optional RFO context
- Automatic duration calculation from start and end time
- Team or wider worker roster depending on role
- Monthly OT usage guidance during submission
- Monthly 72-hour limit warning and submission blocking where applicable

#### Permissions

- `WORKER`: self submit
- `SUPERVISOR`: same-team proxy submit
- `ADMIN`: broader proxy submit scope

### OT List

#### Purpose

The main history page for OT requests.

#### Main Uses

- Review OT request history
- Filter by status and date range
- Open OT detail
- Export CSV where permitted

#### Key Functions

- Status and date filters
- Pagination on desktop
- Detail links with state preservation
- CSV export for supervisor and admin roles

#### Permissions

- Available according to OT visibility rules
- Export is limited to `SUPERVISOR` and `ADMIN`

### OT Detail

#### Purpose

Read and act on a single OT request.

#### Main Uses

- Review request metadata and justification
- Review first-stage and final approval history
- Cancel, endorse, approve, or reject where permitted
- Return to the filtered list without losing context

#### Key Functions

- Back-to-list state preservation
- Role-based action buttons
- Approval history display

#### Permissions

- Own request, same-team supervisor scope, or admin scope as appropriate
- Self-approval is blocked

### OT Approve

#### Purpose

Queue-based approval surface for OT processing.

#### Main Uses

- Supervisors endorse same-team pending requests
- Admins final-approve endorsed requests

#### Key Functions

- Queue cards or rows with inline actions
- Role-specific queue contents
- Clear stage distinction between first-stage and final approval

#### Permissions

- `SUPERVISOR`: same-team `PENDING` items only
- `ADMIN`: `ENDORSED` items awaiting final approval

### Mobile OT Flow

The mobile OT tab uses three internal stages.

- `o1`: submit
- `o2`: history
- `o3`: approve

#### Mobile OT Notes

- `o1` shows the current month's usage against the 72-hour limit and blocks over-limit submission
- `o2` is a card-based history list with status filter chips
- `o2` is a card-based mobile history view with status filters and pagination
- `o2` does not include the desktop export action
- `o3` is role-aware: workers do not see it, supervisors endorse `PENDING` items, and admins approve `ENDORSED` items

## OT Dashboard

### Purpose

The OT Dashboard is the analytics surface for OT usage and pipeline monitoring.

### Main Uses

- Review OT totals and approval flow health
- Inspect monthly usage against the 72-hour limit
- Review OT by reason code
- Review weekly OT trend
- Compare team-level OT patterns

### Key Functions

- Month selector
- Team filter for admins
- Individual monthly OT usage cards or bars
- OT by reason breakdown
- Weekly OT trend
- Summary KPIs and pipeline metrics

### Permissions

- `SUPERVISOR` and `ADMIN` only
- Supervisors stay within team scope
- Admins can switch team scope or view broader data

## RFO

### Purpose

The RFO page is the full work-package analytics surface.

### Main Uses

- Review progress against planned MH
- Monitor blockers and worker allocation
- Inspect burndown and efficiency metrics
- Jump into related task work where relevant

### Key Functions

- Searchable RFO selector
- Summary strip for aircraft and schedule context
- KPI cards for tasks, planned MH, actual MH, variance, OT hours, and blockers
- Burndown view
- Efficiency metrics
- Active blocker list
- Worker allocation view

### Permissions

- `SUPERVISOR` and `ADMIN` only

### Notes

- Historical work packages can still open directly when routed by ID
- If an RFO number is missing in a display context, fallback display behavior may still use the work-package ID

## More

The More area is the mobile utility and secondary-navigation surface.

### More Home

#### Purpose

Acts as the launcher for secondary mobile utilities and account-level pages.

### RFO Summary

#### Purpose

Provides a lightweight RFO summary view for fast mobile reference.

#### Main Uses

- Review progress, overdue count, blockers, and remaining MH
- Jump back into related task work

#### Permissions

- `SUPERVISOR` and `ADMIN` only

### Help

#### Purpose

Provides short operator help content.

#### Typical Topics

- How to update tasks
- How to submit OT
- What to do when save fails or a version conflict appears
- Support contacts

### Font Size

#### Purpose

Lets the user change the mobile reading size.

#### Functions

- Default size
- Large size
- Extra large size

### My Account

#### Purpose

Shows the current user's basic account and assignment context.

#### Typical Contents

- Name
- Employee number
- Team
- Role
- Shop
- Email

### Log out

#### Purpose

Ends the current session from the More area.

## Admin

The Admin area contains system administration pages.

### Personnel Management

#### Purpose

Manage users, teams, roles, email identities, and active status.

#### Key Functions

- Add user
- Edit user
- Activate or deactivate a user
- Protect against destructive admin lockout scenarios where implemented

### Reference Data

#### Purpose

Manage read-mostly operational reference entities.

#### Key Functions

- Aircraft management
- Work package or RFO management
- Shop stream management
- CSV import with row-level validation feedback

### Shops

#### Purpose

Manage task-shop master data.

#### Key Functions

- Create a shop
- Rename display name
- Review shop list

### Shop Access

#### Purpose

Manage which non-admin users can enter task surfaces for which shops.

#### Key Functions

- Grant and revoke shop access
- Set `VIEW`, `EDIT`, or `MANAGE` per shop
- Review invalid or legacy access rows where needed

### Settings

#### Purpose

Manage shared system-level operational configuration.

#### Key Functions

- Set the current working week
- Configure automatic working-week advancement where enabled
- Set the Data Entry update-threshold value used for `NEEDS UPDATE`
- Manage notification-related settings
- Export task data based on the saved working week

## Shared Behaviors

### Current Working Week

`meeting_current_date` is the effective working week for task surfaces.
Task exports should use the saved working week rather than a temporary preview state.

### Shop Access

Shop Access controls entry into task surfaces for non-admin users.
Admins bypass task-surface shop checks.
Read visibility can still be broader than editability in some detail contexts.

### Search

Search should respect feature-level visibility rules.
Task search results should route users to a canonical readable page rather than forcing an edit form the user cannot use.

### Export

- OT CSV export is role-limited
- Task export is based on the saved working week configuration
- Mobile OT history should not be documented as an export surface

### Access-Denied States

Where a user lacks access, the UI should show a clear access-denied page state or disabled navigation rather than a misleading active link.

## Role-Based Daily Workflows

### Admin Workflow

A typical admin flow is:

1. Start on `Dashboard` for the current operational picture
2. Open `Task Manager` to import, create, assign, or audit weekly task work
3. Use `OT Approve` and `OT Dashboard` for endorsement backlog, final approval, and OT trend monitoring
4. Use `RFO` for work-package analytics and blocker review
5. Use `Personnel`, `Reference Data`, `Shops`, `Shop Access`, and `Settings` for system administration

### Supervisor Workflow

A typical supervisor flow is:

1. Start on `Dashboard` for current queue and RFO awareness
2. Use `Data Entry` as the main daily task update surface
3. Review `NEW` and `NEEDS UPDATE` items first
4. Assign workers and update task status, MH, remarks, deadlines, and issue flags
5. Use `OT` pages to submit proxy OT where needed and endorse same-team requests
6. Use `RFO` or `RFO Summary` to review blocker-heavy work packages

### Worker Workflow

A typical worker flow is mobile-first:

1. Use the mobile `Tasks` tab if task shop access has been granted
2. Review assigned aircraft and task cards
3. Use `m3` quick update where edit access is allowed, or `m5` detail where read-only inspection is needed
4. Use the mobile `OT` tab for self-service OT submission and history review
5. Use `More` for Help, Font Size, My Account, or Log out

## Known Operational Constraints

- Automatic week advancement depends on the application process and scheduler configuration being active
- Some task records may remain visible in detail contexts even when edit access is not allowed
- Historical work packages may be reachable by direct link even when they are not listed in the default selector
- If the running UI still differs from the documented mobile OT history behavior, align the deployment or explicitly record the exception

## Document Scope

This guide is intentionally user-facing.
It does not document internal APIs, database tables, test-only routes, or implementation-only developer details.
