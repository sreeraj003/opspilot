"""Request and response contracts for accounts.

Write schemas forbid unknown fields, so a client submitting ``is_superuser``,
``password_hash`` or any other privileged field gets a 422 instead of having
it silently ignored. Read schemas contain no credential material of any kind.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.emails import EmailAddress
from app.models.user import FULL_NAME_MAX_LENGTH, USERNAME_PATTERN
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.password_service import MAX_PASSWORD_LENGTH

Username = Annotated[str, Field(pattern=USERNAME_PATTERN, description="3-64 letters, digits, '.', '_' or '-'.")]
FullName = Annotated[str, Field(min_length=1, max_length=FULL_NAME_MAX_LENGTH)]
# Length is checked by the password policy (with a clear message); this only bounds the request size.
Password = Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH * 4)]

_WRITE_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _strip_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Full name must not be empty.")
    return value


class UserCreate(BaseModel):
    model_config = _WRITE_CONFIG

    email: EmailAddress
    username: Username | None = None
    full_name: FullName
    password: Password = Field(description="Initial password, chosen by the administrator and shared securely.")
    is_active: bool = True

    @field_validator("full_name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _strip_name(value)


class UserUpdate(BaseModel):
    """Fields an administrator may change. Status and privileges have dedicated endpoints."""

    model_config = _WRITE_CONFIG

    email: EmailAddress | None = None
    username: Username | None = None
    full_name: FullName | None = None

    @field_validator("full_name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _strip_name(value) if value is not None else None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str | None
    full_name: str
    is_active: bool
    is_verified: bool
    is_superuser: bool
    is_locked: bool = False
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None


class UserDetail(UserRead):
    active_session_count: int


class UserPage(BaseModel):
    items: list[UserRead]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


class SessionsRevokedResponse(BaseModel):
    revoked_sessions: int


class AdminPasswordResetResponse(BaseModel):
    """Returned to the administrator only. The link is shown once and never stored."""

    reset_url: str | None = Field(
        description="One-time link to hand to the user over a trusted channel. Null when admin links are disabled."
    )
    expires_at: datetime


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "AdminPasswordResetResponse",
    "SessionsRevokedResponse",
    "UserCreate",
    "UserDetail",
    "UserPage",
    "UserRead",
    "UserSessionRead",
    "UserUpdate",
]
