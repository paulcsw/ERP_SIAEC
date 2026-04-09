# ERP Operator Feature Guide

Last reviewed: April 9, 2026

Alignment basis: ERP Integrated SSOT v2.0 (MiniPatch 1~12b-fix3) and ERP Implementation Plan v2 aligned to that SSOT.

## Purpose

This guide explains the current user-facing features of the ERP system from an operator's point of view.
It is organized by page group, not by API or internal module.

Use this document to understand:

- what each page is for
- who can use it
- what actions are available
- what filters, exports, and save flows exist
- how shared behaviors such as shop access and working week settings affect the system

## Roles And Access Model

The ERP uses two different permission layers, and they should not be mixed together.

### User Roles

- `ADMIN`: full system administration, global task access, final OT approval, settings, reference data, shops, shop access, and user management
- `SUPERVISOR`: team-level operational management, OT first-stage endorsement, and task work within granted shop scope
- `WORKER`: self-service OT and task visibility or update capability only where shop access has been granted

### Task Shop Access Levels

These access levels apply only to task surfaces and are granted per shop.
There is no standalone `VIEW` user role.

- `VIEW`: read-only task visibility for the granted shop
- `EDIT`: task update capability for the granted shop
- `MANAGE`: broader task control for the granted shop, including management actions where enabled

### Cross-Cutting Rules

- `Shop Access` controls whether a non-admin user can enter task surfaces for a given shop
- `meeting_current_date` is the effective working week used by Task Manager, Data Entry, and task-related exports

## System Map

The current parent page groups are:

- `Dashboard`
- `Tasks`
- `OT`
- `RFO`
- `More`
- `Admin`

Mobile HTMX steps are described inside their parent groups rather than as separate top-level products.

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

### Mobile Role Notes

- A worker with no task shop access should see OT and More, but not an active task tab
- A worker with `VIEW` task access can open task pages in read-only mode
- A worker with `EDIT` task access can use mobile quick updates where allowed
- Supervisors can use mobile Tasks, OT, and More, including the OT approval segment
- Admins can use the mobile shell, but Task Manager, Personnel, Reference, Settings, OT Dashboard, and full RFO Detail remain desktop-first surfaces

## Dashboard

### Purpose

The dashboard is the operational landing page. It provides a quick summary of current work status, OT activity, and RFO progress.

### Main Uses

- Review current operational KPIs
- Check OT approval pipeline status
- Monitor current OT usage
- Open related OT and RFO pages from summary cards

### Key Functions

- KPI summary cards
- OT approval and OT usage overview
- OT quota and usage signals
- RFO progress snapshot
- Drill-down links into OT and RFO pages

### Access

- Available to logged-in users
- Data scope depends on role and effective shop/team visibility

### Notes

- This is the general operations landing page
- OT-specific analytics live separately in the OT Dashboard

## Tasks

The Tasks group covers `Task Manager`, `Data Entry`, `Task Detail`, and the mobile task flow.

### Common Rules

- Non-admin users need explicit `Shop Access` to enter task surfaces
- `ADMIN` always has task-surface access
- The active working week is driven by `meeting_current_date`
- Search and visibility can be broader than editability; being able to view a task does not always mean being allowed to update it

### Task Manager

#### Purpose

Task Manager is the planning, distribution, and audit hub for the active working week.

#### Main Uses

- Import tasks from RFO or create them directly
- Assign tasks to supervisors
- Review weekly task distribution and execution state
- Inspect work by table, kanban, or RFO-grouped view
- Audit remarks, issues, MH history, and recent changes

#### Key Functions

- Primary actions: `Import RFO`, `Create & Assign`, and `Assign To...`
- Secondary management action: `Init Week`, where enabled
- Working-week filter
- Search across task text, aircraft registration, and RFO
- Shop / airline / supervisor / status / RFO filters
- Table, Kanban, and RFO grouped views
- Pagination with preserved filter state
- Export and audit actions where permitted
- Side-panel detail with distribution, remarks, active issue, MH history, and audit trail

#### Permissions

- `ADMIN`: primary full-use audience
- `SUPERVISOR`: scoped read-oriented access within granted shop visibility
- `WORKER`: not a Task Manager audience

#### Notes

- Task Manager is an admin planning and audit surface, not the main day-to-day update screen
- Default week selection follows the currently visible user scope, not a global unrestricted latest week
- Global search links to task detail rather than forcing Data Entry access

### Data Entry

#### Purpose

Data Entry is the task update surface used for weekly maintenance and in-progress execution tracking.

#### Main Uses

- Update task status, MH, due date, assignee, and note fields
- Add tasks within the selected aircraft context
- Review tasks by aircraft
- Use desktop inline update or mobile step flow

#### Key Functions

- Aircraft-centered browsing
- Inline edit panel below the selected task on desktop
- Read-only versus editable task behavior
- Add Task modal with explicit shop and optional RFO selection
- Shop-scoped worker options
- `Needs Update` highlighting based on threshold config

#### Permissions

- Entry into Data Entry requires task-surface access
- Edit and create actions require explicit edit/manage shop permission
- Read-only users can be routed to task detail rather than edit forms

#### Mobile Task Flow

- `m1`: aircraft list
- `m2`: task list for selected aircraft
- `m3`: mobile quick update for editable tasks
- `m4`: mobile add task
- `m5`: read-only detail view

#### Notes

- Mobile and desktop follow the same underlying task visibility rules
- Worker/assignee visibility is intentionally narrower than full edit access

### Task Detail

#### Purpose

Task Detail is the full read-focused page for one task.

#### Main Uses

- Review task metadata and current state
- Inspect snapshot history
- Review manpower distribution
- Check audit trail and update context

#### Key Functions

- Full task detail page
- History and snapshot sections
- Audit trail
- Distribution and related task context

#### Permissions

- Visible according to task detail visibility rules
- Unauthorized access returns an HTML access-denied state, not raw JSON

## OT

The OT group includes desktop submit/list/detail/approve pages and the mobile OT flow.

### OT Submit

#### Purpose

Used to create new overtime requests.

#### Main Uses

- Workers submit their own requests
- Supervisors and admins submit proxy requests when permitted

#### Key Functions

- Request form with worker, date, time, reason, justification, and optional RFO/work package context
- Team or all-worker roster depending on role
- Monthly OT usage guidance during submission

#### Permissions

- `WORKER`: self submit
- `SUPERVISOR`: same-team proxy submit
- `ADMIN`: all-team proxy submit

### OT List

#### Purpose

The history and management page for OT requests.

#### Main Uses

- Review OT request history
- Filter by status, search, and date range
- Open request detail
- Export CSV where permitted

#### Key Functions

- Status/search/date filters
- Pagination
- Detail links with state preservation
- CSV export for supervisor/admin roles

#### Permissions

- Available according to OT visibility rules
- Export is limited to `SUPERVISOR` and `ADMIN`

### OT Detail

#### Purpose

Detailed view of a single OT request.

#### Main Uses

- Review request metadata and justification
- Cancel, endorse, approve, or reject depending on role and current status
- Return to the filtered list without losing context

#### Key Functions

- Back-to-list state preservation
- Role-based action buttons
- Approval history display

#### Permissions

- Same visibility rules as OT API
- Own request, same-team supervisor scope, or admin scope as appropriate

### OT Approve

#### Purpose

Approval queue for OT processing.

#### Main Uses

- Supervisors endorse pending requests
- Admins final-approve endorsed requests

#### Key Functions

- Inline queue action buttons
- Role-specific queue contents
- Stale queue refresh after status conflicts

#### Permissions

- Supervisor queue for same-team pending items
- Admin queue for endorsed items from other users

### Mobile OT Flow

- `o1`: mobile submit
- `o2`: mobile history list with status filter and pagination
- mobile detail: request detail and actions
- `o3`: mobile endorse/approve queue

### Notes

- Desktop and mobile OT flows are intentionally aligned on visibility and approval rules
- Self-approval is blocked

## OT Dashboard

### Purpose

The OT Dashboard is the analytics surface for OT usage and pipeline monitoring.

### Main Uses

- Review OT totals and trends
- Inspect monthly usage against limits
- Review reason breakdown and weekly trend
- Compare team-level OT patterns

### Key Functions

- Month selector
- Team filter for admins
- Summary KPI cards
- Monthly usage card
- Pipeline and trend visuals

### Permissions

- `SUPERVISOR` and `ADMIN` only
- Supervisors are fixed to their own team scope
- Admins can switch teams or use all-teams scope

### Notes

- Task navigation state still respects task-surface access even though this is not a task page

## RFO

### Purpose

The RFO page is the work-package analytics surface.

### Main Uses

- Review progress against planned MH
- Monitor blockers and worker allocation
- Inspect burndown over time
- Jump to related tasks when an exact RFO link is available

### Key Functions

- Selector for active work packages
- Support for direct-link historical work package review
- Summary strip and KPI metrics
- Task status breakdown
- Blocker list
- Worker allocation
- Burndown chart

### Permissions

- `SUPERVISOR` and `ADMIN` only

### Notes

- Historical selected work packages can still render when opened directly
- Display fallback uses `WP-<id>` if no RFO number exists

## More

The More area is the utility and secondary-navigation surface.

### More Home

#### Purpose

Acts as a utility launcher for secondary tools, help content, account pages, and logout.

### Global Search

#### Purpose

Searches across tasks, OT, and RFO surfaces in one place.

#### Main Uses

- Find a task by text, aircraft, or assignment context
- Find OT requests
- Find RFO/work package records where permitted

#### Key Functions

- Unified search results grouped by entity type
- Result links to the correct SSR surfaces
- RFO links use direct path routes
- Task results open Task Detail, not Data Entry

#### Permissions

- Result visibility follows each feature's own visibility rules

### RFO Summary

#### Purpose

A lighter-weight RFO analytics view from the More section.

#### Main Uses

- Quick review of a selected work package
- Open related tasks when a task filterable RFO number exists

#### Key Functions

- Scoped work package selector
- KPI and summary display
- Related-task CTA into Task Manager when valid

#### Permissions

- `SUPERVISOR` and `ADMIN` only

### Help

- Operator help and support-oriented reference content, including task update guidance, OT request help, and troubleshooting direction for save failures or access issues

### Font Size

- Personal display sizing preference page with operator-focused readability options

### My Account

- Personal account information such as name, employee number, team, roles, shop context, and email where available

### Log out

- Ends the current session and returns the user to the signed-out state

## Admin

The Admin group contains system administration pages.

### Personnel Management

#### Purpose

Manage users, roles, teams, emails, and active status.

#### Key Functions

- Add user
- Edit user safely with multi-role support
- Activate/deactivate users
- Protect against last-admin lockout

#### Notes

- Self-lockout and last-active-admin protections are enforced in both UI and API

### Reference Data

#### Purpose

Manage read-mostly operational reference entities.

#### Key Functions

- Review Aircraft, Work Packages, and Shop Streams
- CSV import with row-level error reporting
- Fallback display for null RFO values

#### Notes

- Import results are surfaced inline, including partial success and row-level failures

### Shops

#### Purpose

Manage shop master data.

#### Key Functions

- Create shop
- Rename shop display name
- Review shop list

#### Notes

- Shop code is immutable after creation

### Shop Access

#### Purpose

Control which non-admin users can enter task surfaces for specific shops.

#### Key Functions

- Grant and revoke access rows
- Set per-shop access level
- Review legacy invalid rows

#### Notes

- `ADMIN` is intentionally excluded from effective shop access management because admin access is global
- Inactive users cannot receive new grants

### Settings

#### Purpose

Manage system-wide operational configuration.

#### Key Functions

- Set the current working week
- Enable or disable automatic week advancement
- Configure automatic advancement day and time
- Configure task update threshold
- Review notification-related settings
- Export task data based on the saved working week

#### Notes

- Automatic advancement uses in-app scheduling
- Working week start remains Monday-based
- Export uses the saved working week, not a temporary preview state

## Cross-Cutting Behaviors

### Current Working Week

- `meeting_current_date` is the effective working week used by task surfaces
- Automatic advancement updates this value on schedule
- Manual override is still allowed through Settings

### Shop Access

- Shop Access controls entry into task surfaces for non-admin users
- `ADMIN` bypasses shop access checks
- Read-only task visibility may still appear in task detail/search contexts even when edit access is not allowed

### Navigation

- Sidebar and mobile task tabs respect `has_task_access`
- Users without task-surface access see disabled task navigation rather than misleading active links

### Search

- Global search links to detail or canonical page routes
- Search visibility is intentionally aligned with each feature's own visibility rules

### Export

- OT CSV export is limited by role
- Task export is based on the saved working week setting

## Role-Based Daily Workflows

### Admin Workflow

A typical admin flow is:

1. Start on `Dashboard` for the current operational picture
2. Open `Task Manager` to import, create, assign, or audit weekly task work
3. Use `OT Approve` and `OT Dashboard` for approval backlog, final approval, and OT trend monitoring
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

- Automatic week advancement depends on the application process being up and running
- Some actions are intentionally read-only or disabled based on shop scope, even if the underlying record is still visible
- Historical work packages can be opened directly even when they are not in the default active selector list

## Document Scope

This guide is intentionally user-facing.
It does not document internal APIs, database tables, or test-only routes.
