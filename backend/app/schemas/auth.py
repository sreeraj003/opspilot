"""Request and response contracts for /api/v1/auth."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.security.passwords import MAX_PASSWORD_LENGTH


def normalize_email(value: str) -> str:
    return value.strip().lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(normalize_email)]
# Login accepts any string so malformed emails fail with the generic credentials
# error rather than a validation error that reveals which field was wrong.
LoginEmail = Annotated[str, StringConstraints(min_length=1, max_length=320), AfterValidator(normalize_email)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: DisplayName = Field(description="Name of the new organization.")
    full_name: DisplayName
    email: NormalizedEmail
    password: Password = Field(description="Must satisfy the password policy.")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: LoginEmail
    password: Password


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(
        default=None,
        max_length=512,
        description="Refresh token. Optional when sent as the HttpOnly refresh cookie.",
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
    refresh_token: str = Field(description="Single-use refresh token; rotated on every refresh.")
    refresh_expires_at: datetime


class OrganizationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    organization: OrganizationSummary
    roles: list[str]
    permissions: list[str]
    last_login_at: datetime | None


class RegisterResponse(BaseModel):
    user: CurrentUserResponse
    tokens: TokenResponse
