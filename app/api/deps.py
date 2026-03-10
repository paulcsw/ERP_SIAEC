"""FastAPI dependencies — auth, DB session, role checks (§8.1.1, §8.2)."""
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db as _get_db
from app.schemas.common import APIError


async def get_current_user(request: Request) -> dict:
    """Return session payload or raise 401 AUTH_REQUIRED."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise APIError(401, "Authentication required", "AUTH_REQUIRED")
    return {
        "user_id": user_id,
        "employee_no": request.session.get("employee_no"),
        "display_name": request.session.get("display_name"),
        "roles": request.session.get("roles", []),
        "team": request.session.get("team"),
    }


def require_role(*roles: str):
    """Dependency factory: current user must have at least one of the given roles."""

    async def _check(current_user: dict = Depends(get_current_user)):
        if not any(r in current_user["roles"] for r in roles):
            raise APIError(403, "Insufficient permissions", "FORBIDDEN")
        return current_user

    return _check


async def get_db():
    async for session in _get_db():
        yield session
