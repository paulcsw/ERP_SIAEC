"""Authentication endpoints — Azure AD OAuth2 Authorization Code Flow (§8.2)."""
import base64
import json
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.user import User
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
    """Decode JWT payload (trusted — token received directly from Azure over HTTPS)."""
    payload = id_token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@router.get("/login")
async def login(request: Request):
    """Redirect to Azure AD authorize endpoint (§8.2.2)."""
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


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Exchange authorization code for tokens, match user, create session (§8.2.1)."""
    # Verify OAuth state
    expected = request.session.pop("oauth_state", None)
    if not expected or expected != state:
        raise APIError(403, "Invalid OAuth state", "CSRF_INVALID")

    # Exchange code for tokens
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

    # User matching — §8.2.1
    # 1st: azure_oid
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles))
        .where(User.azure_oid == oid, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if not user and email:
        # 2nd: email match → auto-map azure_oid
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

    # Build session — §8.2.4
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["employee_no"] = user.employee_no
    request.session["display_name"] = user.name
    request.session["roles"] = [r.name for r in user.roles]
    request.session["team"] = user.team

    # CSRF token — §8.1.2 step 1
    csrf_token = secrets.token_hex(32)
    request.session["csrf_token"] = csrf_token

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key="csrftoken",
        value=csrf_token,
        path="/",
        samesite="lax",
        httponly=False,  # JS needs to read this
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """Clear session and redirect to login (§8.2.2)."""
    request.session.clear()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("csrftoken")
    return response


@router.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Return current user info + roles (§8.2.2)."""
    return current_user
