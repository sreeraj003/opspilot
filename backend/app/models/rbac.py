"""Role-based access control (Module 04).

* ``Role`` and ``PermissionDefinition`` are configured by administrators at
  run time. Rows flagged ``is_system`` are seeded by migrations and protected.
* Assignments (``RolePermission``, ``UserRole``, ``UserPermission``) are never
  deleted: revoking one sets ``revoked_at``, so the history stays queryable.
  A partial unique index allows exactly one *unrevoked* row per pair.
* ``RbacAuditEvent`` is the append-only change log for all of the above.
* ``AuthorizationState`` holds a single monotonically increasing ``revision``
  that every RBAC change increments in the same transaction. The authorization
  cache compares it to decide whether cached grants are still current.

Roles and permissions are soft-deleted (``deleted_at``) so audit rows and
assignment history keep pointing at them; keys and names are unique among
live rows only.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

ROLE_KEY_MAX_LENGTH = 64
ROLE_NAME_MAX_LENGTH = 100
PERMISSION_KEY_MAX_LENGTH = 128
PERMISSION_NAME_MAX_LENGTH = 150
RESOURCE_TYPE_MAX_LENGTH = 64
ACTION_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 2000

# Lower-case identifiers made of segments joined by '.', ':' or '-' (``sales.karnataka.view``, ``users:read``).
ROLE_KEY_PATTERN = r"^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$"
PERMISSION_KEY_PATTERN = r"^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$"
RESOURCE_TYPE_PATTERN = r"^[a-z][a-z0-9_]*$"
ACTION_PATTERN = r"^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$"


class AssignmentType:
    GRANT = "grant"
    DENY = "deny"


class Role(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(f"key ~ '{ROLE_KEY_PATTERN}'", name="key_format"),
        CheckConstraint(f"char_length(btrim(name)) BETWEEN 1 AND {ROLE_NAME_MAX_LENGTH}", name="name_length"),
        CheckConstraint(
            f"description IS NULL OR char_length(description) <= {DESCRIPTION_MAX_LENGTH}", name="description_length"
        ),
        # Keys and names (case-insensitive) are unique installation-wide among live roles.
        Index("ix_roles_key_live", "key", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_roles_name_live", func.lower(text("name")), unique=True, postgresql_where=text("deleted_at IS NULL")),
        {"comment": "Roles configured by administrators. is_system rows are seeded and protected."},
    )

    key: Mapped[str] = mapped_column(String(ROLE_KEY_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(ROLE_NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    def __repr__(self) -> str:
        return f"Role(key={self.key!r})"


class PermissionDefinition(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A permission. Named ``PermissionDefinition`` in Python to avoid confusion with
    ``app.core.authorization.Permission`` (the enum of system permission keys)."""

    __tablename__ = "permissions"
    __table_args__ = (
        CheckConstraint(f"key ~ '{PERMISSION_KEY_PATTERN}'", name="key_format"),
        CheckConstraint(f"resource_type ~ '{RESOURCE_TYPE_PATTERN}'", name="resource_type_format"),
        CheckConstraint(f"action ~ '{ACTION_PATTERN}'", name="action_format"),
        CheckConstraint(
            f"char_length(btrim(name)) BETWEEN 1 AND {PERMISSION_NAME_MAX_LENGTH}", name="name_length"
        ),
        CheckConstraint(
            f"description IS NULL OR char_length(description) <= {DESCRIPTION_MAX_LENGTH}", name="description_length"
        ),
        CheckConstraint("jsonb_typeof(policy_definition) = 'object'", name="policy_is_object"),
        CheckConstraint("octet_length(policy_definition::text) <= 65536", name="policy_size"),
        Index("ix_permissions_key_live", "key", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_permissions_resource_action", "resource_type", "action"),
        {"comment": "Permission definitions. policy_definition is validated structured data, never code or SQL."},
    )

    key: Mapped[str] = mapped_column(String(PERMISSION_KEY_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(PERMISSION_NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(RESOURCE_TYPE_MAX_LENGTH))
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH))
    # Normalised output of app.authorization.policy.validate_policy (includes "version").
    # Assign a new dict to change it: in-place mutation is not tracked.
    policy_definition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Optional: after this moment the permission grants nothing, wherever it is assigned.
    expires_at: Mapped[datetime | None] = mapped_column()

    def __repr__(self) -> str:
        return f"PermissionDefinition(key={self.key!r})"


class RolePermission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        Index(
            "ix_role_permissions_active_pair",
            "role_id",
            "permission_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_role_permissions_permission_id", "permission_id"),
        {"comment": "Permissions granted to roles. Revoked rows are kept as history."},
    )

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column()
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class UserRole(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        Index(
            "ix_user_roles_active_pair", "user_id", "role_id", unique=True, postgresql_where=text("revoked_at IS NULL")
        ),
        Index("ix_user_roles_role_id", "role_id"),
        CheckConstraint("expires_at IS NULL OR expires_at > assigned_at", name="expiry_after_assignment"),
        {"comment": "Roles assigned to users. Revoked rows are kept as history; expired rows grant nothing."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class UserPermission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_permissions"
    __table_args__ = (
        Index(
            "ix_user_permissions_active_pair",
            "user_id",
            "permission_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_user_permissions_permission_id", "permission_id"),
        CheckConstraint("assignment_type IN ('grant', 'deny')", name="assignment_type_valid"),
        CheckConstraint("expires_at IS NULL OR expires_at > assigned_at", name="expiry_after_assignment"),
        CheckConstraint(
            "policy_override IS NULL OR jsonb_typeof(policy_override) = 'object'", name="override_is_object"
        ),
        {"comment": "Permissions granted (or explicitly denied) to one user. Revoked rows are kept as history."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))
    # grant: the user holds the permission. deny: the permission is removed for this
    # user even when one of their roles grants it.
    assignment_type: Mapped[str] = mapped_column(String(8), default=AssignmentType.GRANT)
    # Narrowing-only override of the permission's policy for this user (grants only).
    policy_override: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class RbacEventType:
    ROLE_CREATED = "role_created"
    ROLE_UPDATED = "role_updated"
    ROLE_ACTIVATED = "role_activated"
    ROLE_DEACTIVATED = "role_deactivated"
    ROLE_DELETED = "role_deleted"
    PERMISSION_CREATED = "permission_created"
    PERMISSION_UPDATED = "permission_updated"
    PERMISSION_ACTIVATED = "permission_activated"
    PERMISSION_DEACTIVATED = "permission_deactivated"
    PERMISSION_DELETED = "permission_deleted"
    ROLE_PERMISSION_ASSIGNED = "role_permission_assigned"
    ROLE_PERMISSION_REVOKED = "role_permission_revoked"
    USER_ROLE_ASSIGNED = "user_role_assigned"
    USER_ROLE_REVOKED = "user_role_revoked"
    USER_PERMISSION_ASSIGNED = "user_permission_assigned"
    USER_PERMISSION_REVOKED = "user_permission_revoked"


class RbacAuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rbac_audit_events"
    __table_args__ = (
        Index("ix_rbac_audit_events_created_at", "created_at"),
        Index("ix_rbac_audit_events_role_id", "role_id"),
        Index("ix_rbac_audit_events_permission_id", "permission_id"),
        Index("ix_rbac_audit_events_target_user_id", "target_user_id"),
        {"comment": "Append-only history of role, permission and assignment changes."},
    )

    event_type: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    permission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("permissions.id", ondelete="SET NULL"))
    # Snapshot (keys, names, changed fields, policy before/after) so history stays readable.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)


class AuthorizationState(Base):
    """Single row (id = 1). ``revision`` increases with every committed RBAC change."""

    __tablename__ = "authorization_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        {"comment": "Global authorization revision used to invalidate permission caches."},
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
