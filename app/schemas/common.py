"""Error format (§8.1.5), pagination wrapper (§8.1.3), and shared schema utilities."""
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from starlette.responses import JSONResponse

T = TypeVar("T")


class APIError(Exception):
    """Raise to return {"detail": "...", "code": "...", "field": "..."} response."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        *,
        field: str | None = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.field = field


async def api_error_handler(request, exc: APIError):
    body: dict = {"detail": exc.detail, "code": exc.code}
    if exc.field is not None:
        body["field"] = exc.field
    return JSONResponse(status_code=exc.status_code, content=body)


# ── Pagination (§8.1.3) ──────────────────────────────────────────


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard list response: {"items": [...], "total": N, "page": N, "per_page": N}"""

    items: list[T]
    total: int
    page: int
    per_page: int


def pagination_params(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page (max 200)"),
) -> dict:
    """FastAPI dependency that returns validated {page, per_page, offset}.

    per_page is clamped to 200 even if le=200 is bypassed.
    """
    per_page = min(per_page, 200)
    return {"page": page, "per_page": per_page, "offset": (page - 1) * per_page}
