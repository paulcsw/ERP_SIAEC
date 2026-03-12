# HTMX Full Rewrite Plan (Phased)

## Goal
- Remove page-level JS navigation and `window.location` patterns.
- Move to server-rendered SSR + HTMX partial swaps with stable URL/history.
- Keep role/permission checks server-side and prevent frontend-only state drift.

## Principles
- One page at a time, one interaction cluster at a time.
- Preserve existing HTML structure and visual design while changing interaction wiring.
- Prefer `htmx.ajax()` + `select`/`target` partial swap over body-level replacement.
- Keep non-GET actions (`POST/PATCH`) server-driven and refresh local region only.

## Phase Order
1. Task Manager (`/tasks`) - done baseline, stabilize and harden.
2. OT Desktop (`/ot`, `/ot/new`, `/admin/ot-approve`) - list/filter/paging/actions partialization.
3. Task Entry Desktop (`/tasks/entry`) - stop body-target swaps, move to local region swaps.
4. Admin CRUD pages (`/admin/users`, `/admin/shops`, `/admin/shop-access`, `/admin/reference`) - replace fetch+reload with HTMX forms.
5. RFO/Stats/More refinements - remove remaining direct navigation patterns where needed.
6. Cross-page cleanup - common HTMX helpers, event hooks, regression pass.

## Deliverables Per Phase
- Page-level region container (`id="...-content"`) for partial updates.
- Filter/pagination actions rewritten to HTMX swaps with `pushURL`.
- Modal submit success path refreshes local region only (no full reload).
- New partial template(s) only when needed for detail/side panels.
- Minimal JS helper functions, no duplicated per-row fetch logic.

## Current Status
- Task Manager: migrated to local HTMX content swap and server-side detail partial.
- Task Manager: remaining action handlers (init/import/create/bulk assign) now use shared async request flow and local refresh; CSV export no longer navigates away from page.
- OT Desktop: list/detail/submit/approve desktop flows now use `#ot-content` local swaps and no page reload on success paths.
- OT Desktop: action handlers now share safer async request patterns (consistent error parsing, duplicate-click guard, button busy state) with local content refresh only.
- OT Mobile segments (`_o1/_o2/_o3`): action handlers aligned to same safe async pattern and refresh segment content via HTMX after writes.
- Task Entry (`/tasks/entry`): desktop + mobile actions now use API-aligned async handlers with local region refresh (`#task-entry-content` / `#mob-entry-stage`) and no full-page reload on success paths.
- Admin CRUD: `/admin/users`, `/admin/shops`, `/admin/shop-access`, `/admin/reference` now refresh via HTMX local `#admin-content` swap instead of full-page reload.

## Validation Checklist
- URL query sync and browser back/forward works.
- Sidebar and top layout do not flicker/reset.
- Scroll state remains acceptable on region refresh.
- Role-based button visibility remains server-validated.
- No broken forms due to missing CSRF headers.
