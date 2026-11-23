"""Authentication endpoints under /api/v1/auth."""

from typing import Annotated

from fastapi import APIRouter, Body, Request, Response, status

from app.api.deps import (
    AuthServiceDep,
    ClientInfoDep,
    CurrentPrincipalDep,
    OptionalPrincipalDep,
    SettingsDep,
    UserServiceDep,
)
from app.core.config import Settings
from app.models.user import User
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    OrganizationSummary,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.schemas.errors import ErrorResponse
from app.services.auth_service import IssuedTokens
from app.utils.time import utc_now

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired credentials."}}
_RATE_LIMITED = {429: {"model": ErrorResponse, "description": "Too many attempts; see Retry-After."}}


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_v1_prefix}/auth"


def _set_refresh_cookie(response: Response, settings: Settings, tokens: IssuedTokens) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens.refresh_token,
        max_age=max(0, int((tokens.refresh_expires_at - utc_now()).total_seconds())),
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
    )


def _token_response(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=tokens.refresh_token,
        refresh_expires_at=tokens.refresh_expires_at,
    )


def current_user_response(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        organization=OrganizationSummary.model_validate(user.organization),
        roles=user.role_codes,
        permissions=sorted(user.permission_codes),
        last_login_at=user.last_login_at,
    )


def _refresh_token_from(body: RefreshRequest | None, request: Request, settings: Settings) -> str | None:
    """Prefer an explicit body token; fall back to the HttpOnly refresh cookie."""
    if body is not None and body.refresh_token:
        return body.refresh_token
    return request.cookies.get(settings.refresh_cookie_name)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new organization and its first administrator",
    description=(
        "Self-service sign-up. Creates an organization and a user with the Organization Admin role, "
        "then signs the user in. Disabled when ALLOW_PUBLIC_REGISTRATION is false (403). Returns 409 "
        "when the email is taken and 422 when the password violates the policy."
    ),
    responses={
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        **_RATE_LIMITED,
    },
)
def register(
    data: RegisterRequest,
    response: Response,
    auth: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
) -> RegisterResponse:
    result = auth.register(data, client)
    _set_refresh_cookie(response, settings, result.tokens)
    return RegisterResponse(user=current_user_response(result.user), tokens=_token_response(result.tokens))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in with email and password",
    description=(
        "Returns a short-lived JWT access token and a single-use refresh token. The refresh token is "
        "also set as an HttpOnly, SameSite=Strict cookie scoped to /auth. Any credential problem "
        "(unknown email, wrong password, disabled account) returns the same generic 401."
    ),
    responses={**_UNAUTHORIZED, **_RATE_LIMITED},
)
def login(
    data: LoginRequest, response: Response, auth: AuthServiceDep, client: ClientInfoDep, settings: SettingsDep
) -> TokenResponse:
    result = auth.login(data, client)
    _set_refresh_cookie(response, settings, result.tokens)
    return _token_response(result.tokens)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh token and issue a new access token",
    description=(
        "Accepts the refresh token in the body or the refresh cookie. Each refresh token works once; "
        "presenting a used token again is treated as theft and revokes the whole session."
    ),
    responses=_UNAUTHORIZED,
)
def refresh(
    response: Response,
    auth: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    request: Request,
    body: Annotated[RefreshRequest | None, Body()] = None,
) -> TokenResponse:
    result = auth.refresh(_refresh_token_from(body, request, settings), client)
    _set_refresh_cookie(response, settings, result.tokens)
    return _token_response(result.tokens)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of the current session",
    description=(
        "Revokes the session identified by the bearer access token and/or the refresh token (body or "
        "cookie). Always returns 204, so it reveals nothing about token validity."
    ),
)
def logout(
    auth: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    principal: OptionalPrincipalDep,
    request: Request,
    body: Annotated[RefreshRequest | None, Body()] = None,
) -> Response:
    auth.logout(principal=principal, raw_refresh_token=_refresh_token_from(body, request, settings), client=client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of every session of the current user",
    responses=_UNAUTHORIZED,
)
def logout_all(
    principal: CurrentPrincipalDep, auth: AuthServiceDep, client: ClientInfoDep, settings: SettingsDep
) -> Response:
    auth.logout_all(principal, client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="The authenticated user, organization, roles and effective permissions",
    responses=_UNAUTHORIZED,
)
def me(principal: CurrentPrincipalDep, users: UserServiceDep) -> CurrentUserResponse:
    return current_user_response(users.get_user(principal, principal.user_id))
