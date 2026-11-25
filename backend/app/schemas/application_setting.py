"""Request and response contracts for application settings.

Settings hold non-secret configuration only. Secret-looking keys (``*password*``,
``*token*``, ``*api_key*``, ...) are rejected on input, at the top level and inside
JSON objects, and redacted on output as a second line of defence.
"""

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.core.logging import REDACTED, is_sensitive_key
from app.models.application_setting import SETTING_DESCRIPTION_MAX_LENGTH, SETTING_KEY_MAX_LENGTH
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

SETTING_KEY_PATTERN = r"^[a-z][a-z0-9_.-]*$"
# Limit on the compact JSON encoding of a value.
SETTING_VALUE_MAX_BYTES = 16384
_MAX_NESTING_DEPTH = 10

_KEY_RE = re.compile(SETTING_KEY_PATTERN)

SettingKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=SETTING_KEY_MAX_LENGTH,
        pattern=SETTING_KEY_PATTERN,
        description="Lowercase letters, digits, '.', '_' and '-', starting with a letter.",
        examples=["ui.default_page_size"],
    ),
]


def setting_key_problem(key: str) -> str | None:
    """Why ``key`` is not an acceptable setting key, or ``None`` if it is."""
    if not key or len(key) > SETTING_KEY_MAX_LENGTH or not _KEY_RE.fullmatch(key):
        return "Setting keys use lowercase letters, digits, '.', '_' and '-', start with a letter and are at most 128 characters."
    if is_sensitive_key(key):
        return "Settings must not hold secrets or credentials; this key looks like one."
    return None


def _find_sensitive_keys(value: Any, depth: int = 0) -> list[str]:
    if depth > _MAX_NESTING_DEPTH:
        raise ValueError(f"value is nested more than {_MAX_NESTING_DEPTH} levels deep")
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                found.append(str(key))
            found.extend(_find_sensitive_keys(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_sensitive_keys(item, depth + 1))
    return found


def redact(value: Any) -> Any:
    """Copy of ``value`` with every secret-looking object member replaced by ``[REDACTED]``."""
    if isinstance(value, dict):
        return {key: REDACTED if is_sensitive_key(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class ApplicationSettingWrite(BaseModel):
    """Body of ``PUT /api/v1/settings/{key}``."""

    # hide_input_in_errors: a rejected value may be the very secret it was rejected for.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    value: JsonValue = Field(description="Any JSON value except null.", examples=[25])
    description: str | None = Field(default=None, max_length=SETTING_DESCRIPTION_MAX_LENGTH)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: JsonValue) -> JsonValue:
        if value is None:
            raise ValueError("value must not be null; delete the setting instead")
        if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()) > SETTING_VALUE_MAX_BYTES:
            raise ValueError(f"value must be at most {SETTING_VALUE_MAX_BYTES} bytes of JSON")
        sensitive = _find_sensitive_keys(value)
        if sensitive:
            # Names the offending member names, never their values.
            raise ValueError("settings must not hold secrets or credentials (found: " + ", ".join(sorted(set(sensitive))) + ")")
        return value

    @field_validator("description")
    @classmethod
    def _blank_description_is_none(cls, value: str | None) -> str | None:
        return value or None


class ApplicationSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    value: JsonValue
    description: str | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _redact_secrets(self) -> "ApplicationSettingRead":
        # Input validation keeps secrets out; this protects against rows written any other way.
        self.value = REDACTED if is_sensitive_key(self.key) else redact(self.value)
        return self


class ApplicationSettingPage(BaseModel):
    items: list[ApplicationSettingRead]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "SETTING_KEY_PATTERN",
    "ApplicationSettingPage",
    "ApplicationSettingRead",
    "ApplicationSettingWrite",
    "SettingKey",
    "setting_key_problem",
]
