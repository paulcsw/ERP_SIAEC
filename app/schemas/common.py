"""Error format (§8.1.5) and shared schema utilities."""
from starlette.responses import JSONResponse


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
