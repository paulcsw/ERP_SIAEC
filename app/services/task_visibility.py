"""Shared task visibility helpers for SSR views."""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskItem
from app.models.user_shop_access import UserShopAccess


async def get_allowed_shop_ids(db: AsyncSession, user: dict) -> set[int] | None:
    """Return scoped shop ids for non-admin users; None means full access."""
    if "ADMIN" in user.get("roles", []):
        return None
    rows = (
        await db.execute(
            select(UserShopAccess.shop_id).where(
                UserShopAccess.user_id == user["user_id"]
            )
        )
    ).all()
    return {row.shop_id for row in rows}


async def get_entry_editable_shop_ids(db: AsyncSession, user: dict) -> set[int] | None:
    """Return explicit EDIT/MANAGE shop ids for Data Entry mutations."""
    if "ADMIN" in user.get("roles", []):
        return None
    rows = (
        await db.execute(
            select(UserShopAccess.shop_id, UserShopAccess.access).where(
                UserShopAccess.user_id == user["user_id"]
            )
        )
    ).all()
    return {
        row.shop_id for row in rows
        if row.access in ("EDIT", "MANAGE")
    }


def build_entry_visibility_clause(
    user: dict,
    allowed_shop_ids: set[int] | None,
):
    """Visibility for Data Entry/search SSR: shop access or direct assignment."""
    if "ADMIN" in user.get("roles", []):
        return None
    uid = user["user_id"]
    predicates = [
        TaskItem.assigned_supervisor_id == uid,
        TaskItem.assigned_worker_id == uid,
    ]
    if allowed_shop_ids:
        predicates.append(TaskItem.shop_id.in_(allowed_shop_ids))
    return or_(*predicates)


def can_view_task_item(user: dict, allowed_shop_ids: set[int] | None, task: TaskItem) -> bool:
    """Return whether the user can view the task detail surface."""
    if "ADMIN" in user.get("roles", []):
        return True
    uid = user["user_id"]
    if allowed_shop_ids and task.shop_id in allowed_shop_ids:
        return True
    if task.assigned_supervisor_id == uid or task.assigned_worker_id == uid:
        return True
    return False


def can_edit_task_item(
    user: dict,
    editable_shop_ids: set[int] | None,
    task: TaskItem,
) -> bool:
    """Return whether the user can edit task snapshots for this task."""
    if "ADMIN" in user.get("roles", []):
        return True
    return bool(editable_shop_ids and task.shop_id in editable_shop_ids)
