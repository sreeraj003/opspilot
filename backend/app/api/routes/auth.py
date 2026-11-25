"""Authentication endpoints (``/api/v1/auth``).

Token transport:

* The access token is returned in the JSON body and sent back by the browser
  app as ``Authorization: Bearer ...``. The app keeps it in memory only.
* The refresh token is only ever in an ``HttpOnly`` cookie scoped to
  ``/api/v1/auth`` (``Secure`` and ``SameSite=Strict`` by default), so page
  scripts cannot read it and it is not sent to any other endpoint.

CSRF: the two endpoints authenticated by that cookie (``/refresh`` and
``/logout``) additionally require the ``X-OpsPilot-CSRF: 1`` header, which a
cross-site form cannot send and a cross-origin script can only send after a
CORS preflight that only the configured origins pass, and they reject requests
whose ``Origin`` is not an allowed origin. Bearer-authenticated endpoints need
no CSRF defence: browsers never attach the bearer token automatically.
"""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.api.deps import AuthServiceDep, ClientInfoDep, CurrentPrincipalDep, RateLimiterDep, SettingsDep
from app.core.config import Settings
from app.core.emails import try_normalize_email
from app.core.exceptions import AppError, AuthenticationError, TooManyRequestsError
from app.core.rate_limit import RateLimiter
from app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUser,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.errors import ErrorResponse
from app.services.auth_service import AuthResult, AuthService
from app.api.presenters import current_user_read

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "opspilot_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER = "X-OpsPilot-CSRF"

FORGOT_PASSWORD_MESSAGE = (
    "If an active account exists for that email address, password reset instructions have been sent "
    "to its owner or an administrator has been notified."
)

_401 = {401: {"model": ErrorResponse, "description": "Not authenticated, or the credentials are invalid."}}
_429 = {429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After."}}
_403_CSRF = {403: {"model": ErrorResponse, "description": "Missing CSRF header or disallowed Origin."}}


class CsrfError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "CSRF_FAILED"
    message = "The request was rejected by cross-site request forgery protection."


# --- helpers ----------------------------------------------------------------------------------


def _enforce(limiter: RateLimiter, key: str, limit: int, settings: Settings) -> None:
    result = limiter.hit(key, limit=limit, window_seconds=settings.auth_rate_limit_window_seconds)
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def verify_csrf(request: Request, settings: SettingsDep) -> None:
    """CSRF defence for cookie-authenticated endpoints (see module docstring)."""
    if request.headers.get(CSRF_HEADER) != "1":
        raise CsrfError()
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer:
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
    if origin is None:
        return  # not sent by a browser cross-site (browsers send Origin on cross-origin POST)
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin.rstrip("/") not in {*settings.cors_origins, own_origin}:
        raise CsrfError()


CsrfDep = Annotated[None, Depends(verify_csrf)]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME, include_in_schema=False)]


def _set_refresh_cookie(response: Response, result: AuthResult, settings: Settings) -> None:
    max_age = max(1, int((result.refresh_expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        result.refresh_token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        domain=settings.auth_cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        domain=settings.auth_cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _clear_cookie_header(settings: Settings) -> str:
    response = Response()
    _clear_refresh_cookie(response, settings)
    return response.headers["set-cookie"]


async def _token_response(service: AuthService, result: AuthResult) -> TokenResponse:
    return TokenResponse(
        access_token=result.access.token,
        expires_in=result.access.expires_in,
        user=current_user_read(result.user, await service.permissions_for(result.user)),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


# --- endpoints --------------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse, summary="Sign in", responses={**_401, **_429})
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
) -> TokenResponse:
    account_key = f"login:account:{try_normalize_email(body.email) or body.email.strip().lower()}"
    _enforce(limiter, f"login:ip:{_client_key(request)}", settings.auth_rate_limit_per_ip, settings)
    _enforce(limiter, account_key, settings.auth_rate_limit_per_account, settings)
    result = await service.login(body.email, body.password, client)
    limiter.reset(account_key)
    _set_refresh_cookie(response, result, settings)
    _no_store(response)
    return await _token_response(service, result)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh token and issue a new access token",
    responses={**_401, **_403_CSRF, **_429, 409: {"model": ErrorResponse, "description": "Concurrent refresh; retry."}},
)
async def refresh(
    _csrf: CsrfDep,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    _enforce(limiter, f"refresh:ip:{_client_key(request)}", settings.auth_refresh_rate_limit_per_ip, settings)
    if not refresh_token:
        raise AuthenticationError("Your session has expired. Sign in again.")
    try:
        result = await service.refresh(refresh_token, client)
    except AuthenticationError as exc:
        # Drop the dead cookie so the browser stops presenting it.
        exc.headers = {**(exc.headers or {}), "Set-Cookie": _clear_cookie_header(settings)}
        raise
    _set_refresh_cookie(response, result, settings)
    _no_store(response)
    return await _token_response(service, result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of this browser",
    responses={**_403_CSRF},
)
async def logout(
    _csrf: CsrfDep,
    service: AuthServiceDep,
    settings: SettingsDep,
    client: ClientInfoDep,
    refresh_token: RefreshCookie = None,
) -> Response:
    await service.logout(refresh_token, client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of every session of the current user",
    responses={**_401},
)
async def logout_all(
    principal: CurrentPrincipalDep, service: AuthServiceDep, settings: SettingsDep, client: ClientInfoDep
) -> Response:
    await service.logout_all(principal, client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.get("/me", response_model=CurrentUser, summary="The signed-in user", responses={**_401})
async def me(principal: CurrentPrincipalDep) -> CurrentUser:
    return current_user_read(principal.user, principal.permissions)


@router.post(
    "/change-password",
    response_model=TokenResponse,
    summary="Change the signed-in user's password",
    description="Requires the current password. Ends every session, then starts a new one for this browser.",
    responses={**_401, **_429, 422: {"model": ErrorResponse, "description": "Wrong current password or weak new one."}},
)
async def change_password(
    body: ChangePasswordRequest,
    principal: CurrentPrincipalDep,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
) -> TokenResponse:
    _enforce(limiter, f"password-change:user:{principal.user.id}", settings.auth_rate_limit_per_account, settings)
    result = await service.change_password(principal, body.current_password, body.new_password, client)
    _set_refresh_cookie(response, result, settings)
    _no_store(response)
    return await _token_response(service, result)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset",
    description="Always answers the same way, whether or not the account exists.",
    responses={**_429},
)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
) -> MessageResponse:
    _enforce(limiter, f"forgot:ip:{_client_key(request)}", settings.auth_rate_limit_per_ip, settings)
    account = try_normalize_email(body.email) or body.email.strip().lower()
    _enforce(limiter, f"forgot:account:{account}", settings.auth_rate_limit_per_account, settings)
    await service.request_password_reset(body.email, client)
    return MessageResponse(message=FORGOT_PASSWORD_MESSAGE)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password with a single-use reset token",
    responses={
        **_429,
        400: {"model": ErrorResponse, "description": "The token is invalid, used or expired."},
        422: {"model": ErrorResponse, "description": "The new password does not meet the policy."},
    },
)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
) -> MessageResponse:
    _enforce(limiter, f"reset:ip:{_client_key(request)}", settings.auth_rate_limit_per_ip, settings)
    await service.reset_password(body.token, body.new_password, client)
    return MessageResponse(message="Your password has been reset. Sign in with your new password.")
