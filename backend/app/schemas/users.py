"""Request and response contracts for user and role management."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.auth import DisplayName, NormalizedEmail, Password

RoleCode = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class UserResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    roles: list[str]
    created_at: datetime
    last_login_at: datetime | None
    deactivated_at: datetime | None


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    full_name: DisplayName
    password: Password = Field(description="Initial password; must satisfy the password policy.")
    roles: list[RoleCode] = Field(min_length=1, max_length=10)


class UserRolesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[RoleCode] = Field(min_length=1, max_length=10)


class RoleResponse(BaseModel):
    code: str
    name: str
    description: str
    permissions: list[str]
