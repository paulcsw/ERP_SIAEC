"""Authentication endpoints: Azure AD OAuth2 + local dev login."""

import base64
import json
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.user import Role, User
from app.schemas.common import APIError

router = APIRouter(tags=["auth"])

_AUTH_URL = (
    f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
    "/oauth2/v2.0/authorize"
)
_TOKEN_URL = (
    f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
    "/oauth2/v2.0/token"
)


def _decode_id_token_payload(id_token: str) -> dict:
    """Decode JWT payload from Azure id_token."""
    payload = id_token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _start_session(request: Request, user: User, redirect_to: str) -> RedirectResponse:
    """Store user info in session and return a redirect response."""
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["employee_no"] = user.employee_no
    request.session["display_name"] = user.name
    request.session["roles"] = [r.name for r in user.roles]
    request.session["team"] = user.team

    csrf_token = secrets.token_hex(32)
    request.session["csrf_token"] = csrf_token

    response = RedirectResponse(redirect_to, status_code=302)
    response.set_cookie(
        key="csrftoken",
        value=csrf_token,
        path="/",
        samesite="lax",
        httponly=False,  # JS reads this for double-submit CSRF.
    )
    return response


@router.get("/login")
async def login(request: Request):
    """Redirect to Azure AD authorize endpoint."""
    state = secrets.token_hex(16)
    request.session["oauth_state"] = state
    params = urllib.parse.urlencode(
        {
            "client_id": settings.AZURE_CLIENT_ID,
            "redirect_uri": settings.AZURE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
        }
    )
    return RedirectResponse(f"{_AUTH_URL}?{params}")


@router.get("/dev/login")
async def dev_login(
    request: Request,
    role: str = Query("ADMIN", pattern="^(WORKER|SUPERVISOR|ADMIN)$"),
    employee_no: str = Query("DEV001", min_length=1, max_length=20),
    name: str = Query("Dev User", min_length=1, max_length=100),
    team: str = Query("DEV", max_length=50),
    next_url: str = Query("/tasks/entry", alias="next"),
    db: AsyncSession = Depends(get_db),
):
    """Local-only login bypass for development/testing."""
    if not settings.DEV_LOGIN_ENABLED:
        raise APIError(404, "Not found", "NOT_FOUND")

    if not next_url.startswith("/"):
        next_url = "/tasks/entry"

    role_obj = (
        await db.execute(select(Role).where(Role.name == role))
    ).scalar_one_or_none()
    if not role_obj:
        raise APIError(500, f"Role not found: {role}", "ROLE_NOT_FOUND")

    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.employee_no == employee_no)
        )
    ).scalar_one_or_none()

    if not user:
        user = User(
            employee_no=employee_no,
            name=name,
            team=team,
            is_active=True,
        )
        user.roles = [role_obj]
        db.add(user)
    else:
        user.name = name
        user.team = team
        user.is_active = True
        user.roles = [role_obj]

    await db.commit()

    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.employee_no == employee_no)
        )
    ).scalar_one()

    return _start_session(request, user, next_url)


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Exchange authorization code for tokens, then create session."""
    expected = request.session.pop("oauth_state", None)
    if not expected or expected != state:
        raise APIError(403, "Invalid OAuth state", "CSRF_INVALID")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "client_id": settings.AZURE_CLIENT_ID,
                "client_secret": settings.AZURE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.AZURE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise APIError(401, "Token exchange failed", "AUTH_REQUIRED")

    tokens = resp.json()
    claims = _decode_id_token_payload(tokens["id_token"])
    oid = claims.get("oid")
    email = claims.get("preferred_username") or claims.get("email")

    result = await db.execute(
        select(User)
        .options(selectinload(User.roles))
        .where(User.azure_oid == oid, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if not user and email:
        result = await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(
                User.email == email,
                User.azure_oid.is_(None),
                User.is_active == True,  # noqa: E712
            )
        )
        user = result.scalar_one_or_none()
        if user:
            user.azure_oid = oid
            await db.commit()

    if not user:
        raise APIError(
            403,
            "User not registered in the system. Contact administrator.",
            "USER_NOT_REGISTERED",
        )

    return _start_session(request, user, "/")


@router.post("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("csrftoken")
    return response


@router.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Return current user info + roles."""
    return current_user
