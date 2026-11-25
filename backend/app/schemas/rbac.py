"""Request and response contracts for roles, permissions, assignments and authorization.

Write schemas forbid unknown fields. ``policy_definition`` and
``policy_override`` are accepted as JSON objects here and then validated
structurally by ``app.authorization.policy``; nothing is stored unvalidated.
Authorization decisions are response-only: no endpoint accepts one.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.models.rbac import (
    ACTION_MAX_LENGTH,
    ACTION_PATTERN,
    DESCRIPTION_MAX_LENGTH,
    PERMISSION_KEY_MAX_LENGTH,
    PERMISSION_KEY_PATTERN,
    PERMISSION_NAME_MAX_LENGTH,
    RESOURCE_TYPE_MAX_LENGTH,
    RESOURCE_TYPE_PATTERN,
    ROLE_KEY_MAX_LENGTH,
    ROLE_KEY_PATTERN,
    ROLE_NAME_MAX_LENGTH,
)
from app.repositories.base_repository import MAX_PAGE_SIZE

_WRITE_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("Must not be empty.")
    return value


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


RoleKey = Annotated[str, Field(min_length=2, max_length=ROLE_KEY_MAX_LENGTH, pattern=ROLE_KEY_PATTERN)]
RoleName = Annotated[str, Field(min_length=1, max_length=ROLE_NAME_MAX_LENGTH), AfterValidator(_clean_text)]
PermissionKey = Annotated[
    str, Field(min_length=2, max_length=PERMISSION_KEY_MAX_LENGTH, pattern=PERMISSION_KEY_PATTERN)
]
PermissionName = Annotated[
    str, Field(min_length=1, max_length=PERMISSION_NAME_MAX_LENGTH), AfterValidator(_clean_text)
]
ResourceType = Annotated[str, Field(min_length=1, max_length=RESOURCE_TYPE_MAX_LENGTH, pattern=RESOURCE_TYPE_PATTERN)]
Action = Annotated[str, Field(min_length=1, max_length=ACTION_MAX_LENGTH, pattern=ACTION_PATTERN)]
Description = Annotated[str, Field(max_length=DESCRIPTION_MAX_LENGTH), AfterValidator(_clean_description)]


# --- roles -------------------------------------------------------------------------------------


class RoleCreate(BaseModel):
    model_config = _WRITE_CONFIG

    key: RoleKey
    name: RoleName
    description: Description | None = None
    is_active: bool = True


class RoleUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    key: RoleKey | None = None
    name: RoleName | None = None
    description: Description | None = None
    is_active: bool | None = None


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    is_system: bool
    is_active: bool


class RoleRead(RoleSummary):
    description: str | None
    user_count: int = 0
    permission_count: int = 0
    created_at: datetime
    updated_at: datetime


class RolePage(BaseModel):
    items: list[RoleRead]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    actor_user_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    role_id: uuid.UUID | None
    permission_id: uuid.UUID | None
    details: dict[str, Any]
    created_at: datetime


class RoleUserRead(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    assigned_at: datetime
    expires_at: datetime | None


# --- permissions --------------------------------------------------------------------------------


class PermissionCreate(BaseModel):
    model_config = _WRITE_CONFIG

    key: PermissionKey
    name: PermissionName
    description: Description | None = None
    resource_type: ResourceType
    action: Action
    policy_definition: dict[str, Any] = Field(
        default_factory=dict, description="Structured policy; see docs/PERMISSION_POLICY.md. Validated server-side."
    )
    is_active: bool = True
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


class PermissionUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    name: PermissionName | None = None
    description: Description | None = None
    resource_type: ResourceType | None = None
    action: Action | None = None
    policy_definition: dict[str, Any] | None = None
    is_active: bool | None = None
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


class PermissionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    resource_type: str
    action: str
    is_system: bool
    is_active: bool


class PermissionRead(PermissionSummary):
    description: str | None
    policy_definition: dict[str, Any]
    expires_at: datetime | None
    role_count: int = 0
    user_count: int = 0
    created_at: datetime
    updated_at: datetime


class PermissionPage(BaseModel):
    items: list[PermissionRead]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


class PermissionDetail(PermissionRead):
    roles: list[RoleSummary]
    history: list[AuditEventRead]


class RolePermissionRead(PermissionSummary):
    description: str | None
    assigned_at: datetime


class RoleDetail(RoleRead):
    permissions: list[RolePermissionRead]
    users: list[RoleUserRead]
    history: list[AuditEventRead]


class PolicyValidationRequest(BaseModel):
    model_config = _WRITE_CONFIG

    resource_type: ResourceType
    policy_definition: dict[str, Any]


class PolicyValidationResponse(BaseModel):
    valid: bool = True
    policy_definition: dict[str, Any] = Field(description="The normalised policy that would be stored.")


# --- assignments ---------------------------------------------------------------------------------


class RolePermissionsAssign(BaseModel):
    model_config = _WRITE_CONFIG

    permission_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class RolePermissionsReplace(BaseModel):
    model_config = _WRITE_CONFIG

    permission_ids: list[uuid.UUID] = Field(max_length=500)


class RolePermissionsChanged(BaseModel):
    added: int
    removed: int


class UserRoleAssign(BaseModel):
    model_config = _WRITE_CONFIG

    role_id: uuid.UUID
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


class UserRoleRead(BaseModel):
    role: RoleSummary
    assigned_at: datetime
    assigned_by: uuid.UUID | None
    expires_at: datetime | None
    is_expired: bool


class UserPermissionAssign(BaseModel):
    model_config = _WRITE_CONFIG

    permission_id: uuid.UUID
    assignment_type: Literal["grant", "deny"] = "grant"
    expires_at: datetime | None = None
    policy_override: dict[str, Any] | None = Field(
        default=None, description="Optional narrowing of a data permission for this user only (grants only)."
    )

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


class UserPermissionRead(BaseModel):
    permission: PermissionSummary
    assignment_type: str
    policy_override: dict[str, Any] | None
    assigned_at: datetime
    assigned_by: uuid.UUID | None
    expires_at: datetime | None


# --- effective permissions and decisions ------------------------------------------------------------


class EffectiveRoleRead(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    expires_at: datetime | None


class EffectiveGrantRead(BaseModel):
    key: str
    name: str
    resource_type: str
    action: str
    effect: str
    source: Literal["role", "direct"]
    role_key: str | None
    expires_at: datetime | None


class RowFilterRead(BaseModel):
    table: str
    filter: dict[str, Any]


class TableAccessRead(BaseModel):
    table: str
    columns: list[str]
    denied_columns: list[str]
    operations: list[str]
    row_filter: dict[str, Any] | None
    allow_export: bool
    allow_sql_view: bool


class DataAccessRead(BaseModel):
    action: str
    tables: list[TableAccessRead]


class ResourceAccessRead(BaseModel):
    resource_type: str
    action: str
    allow_export: bool
    allow_sql_view: bool


class EffectivePermissionsRead(BaseModel):
    """What the server currently grants a user. For display and review only: never accepted as input."""

    user_id: uuid.UUID
    policy_revision: int
    evaluated_at: datetime
    is_active: bool
    is_administrator: bool
    roles: list[EffectiveRoleRead]
    system_permissions: list[str]
    permission_keys: list[str]
    revoked_permission_keys: list[str]
    grants: list[EffectiveGrantRead]
    restrictions: list[EffectiveGrantRead]
    data_access: list[DataAccessRead]
    resources: list[ResourceAccessRead]


class AuthorizationCheckRequest(BaseModel):
    """Diagnostic check. ``user_id`` defaults to the caller; another user needs ``access:review``."""

    model_config = _WRITE_CONFIG

    user_id: uuid.UUID | None = None
    resource_type: ResourceType
    action: Action
    tables: list[Annotated[str, Field(min_length=1, max_length=130)]] = Field(default_factory=list, max_length=50)
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=500)
    operation: Literal["read", "aggregate"] | None = None
    export: bool = False
    sql_view: bool = False


class AuthorizationDecisionRead(BaseModel):
    """A diagnostic copy of a server decision. It authorizes nothing by itself."""

    diagnostic: bool = True
    allowed: bool
    decision_reason: str
    user_id: uuid.UUID | None
    resource: str
    action: str
    policy_revision: int
    evaluated_at: datetime
    allowed_tables: list[str]
    allowed_columns: list[str]
    denied_columns: list[str]
    allowed_operations: list[str]
    row_filters: list[RowFilterRead]
    allow_export: bool
    allow_sql_view: bool
    matched_permissions: list[str]


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("Include a time zone offset (for example 2026-12-31T23:59:00Z).")
    return value
