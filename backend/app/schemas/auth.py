"""Request and response contracts for authentication."""

from pydantic import BaseModel, ConfigDict, Field

from app.core.emails import EmailAddress
from app.models.user import EMAIL_MAX_LENGTH
from app.schemas.user import Password, UserRead

_WRITE_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LoginRequest(BaseModel):
    model_config = _WRITE_CONFIG

    # Deliberately not validated as an email here: a malformed address must get
    # the same generic 401 as a wrong password, not a different error.
    email: str = Field(min_length=1, max_length=EMAIL_MAX_LENGTH + 10)
    password: Password


class CurrentUser(UserRead):
    permissions: list[str] = Field(
        description="Permissions the server grants this user. For display only; the server enforces them."
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds.")
    user: CurrentUser


class ChangePasswordRequest(BaseModel):
    model_config = _WRITE_CONFIG

    current_password: Password
    new_password: Password


class ForgotPasswordRequest(BaseModel):
    model_config = _WRITE_CONFIG

    email: str = Field(min_length=1, max_length=EMAIL_MAX_LENGTH + 10)


class ResetPasswordRequest(BaseModel):
    model_config = _WRITE_CONFIG

    token: str = Field(min_length=20, max_length=200)
    new_password: Password


class MessageResponse(BaseModel):
    message: str


__all__ = [
    "ChangePasswordRequest",
    "CurrentUser",
    "EmailAddress",
    "ForgotPasswordRequest",
    "LoginRequest",
    "MessageResponse",
    "ResetPasswordRequest",
    "TokenResponse",
]
