"""Administrative (system) permissions and the authorization-policy contract.

Routes never inspect ``is_superuser`` or roles themselves. They declare the
system permission they need with ``require_permission(Permission.X)`` (see
``app.api.deps``) and the active ``AuthorizationPolicy`` decides.

Module 03 introduced this contract with ``BootstrapAuthorizationPolicy``
(installation administrator: everything; everyone else: nothing). Module 04
plugs in the database-driven engine (``app.authorization.engine``): system
permissions are rows in the ``permissions`` table, granted through roles.

System permissions are the only permissions defined in code, because routes
reference them. Business permissions (what data a user may query) are created
by administrators at run time and never appear here.

Delegation model
----------------
* ``delegable`` system permissions (read-only views) may be added to any
  custom role, so an administrator can let, say, an auditor read the user list.
* Every other system permission is *administrator-only*: it is held through
  the protected ``administrator`` system role (or by the installation
  administrator account) and can never be assigned to a custom role or
  directly to a user. This is the "controlled administrative policy": only
  administrators can change users, roles, permissions or assignments.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.models.user import User

ADMINISTRATOR_ROLE_KEY = "administrator"
SYSTEM_RESOURCE_TYPE = "system"


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    SETTINGS_READ = "settings:read"
    SETTINGS_MANAGE = "settings:manage"
    ROLES_READ = "roles:read"
    ROLES_MANAGE = "roles:manage"
    PERMISSIONS_READ = "permissions:read"
    PERMISSIONS_MANAGE = "permissions:manage"
    ACCESS_REVIEW = "access:review"
    ACCESS_ASSIGN = "access:assign"
    # Module 06: schema registry. Named as the module specification names them.
    SCHEMA_READ = "schema.read"
    SCHEMA_CREATE = "schema.create"
    SCHEMA_UPDATE = "schema.update"
    SCHEMA_DELETE = "schema.delete"
    SCHEMA_IMPORT = "schema.import"
    SCHEMA_EXPORT = "schema.export"
    SCHEMA_RELATIONSHIP_MANAGE = "schema.relationship.manage"
    # Module 07: external GET API connectors.
    CONNECTOR_READ = "connector.read"
    CONNECTOR_CREATE = "connector.create"
    CONNECTOR_UPDATE = "connector.update"
    CONNECTOR_DELETE = "connector.delete"
    CONNECTOR_TEST = "connector.test"
    CONNECTOR_ACTIVATE = "connector.activate"
    # Module 08: AI provider administration.
    AI_PROVIDER_READ = "ai.provider.read"
    AI_PROVIDER_TEST = "ai.provider.test"
    AI_USAGE_READ = "ai.usage.read"
    # Module 09: question understanding and business terminology.
    AI_QUESTION_UNDERSTAND = "ai.question.understand"
    AI_QUESTION_PREVIEW = "ai.question.preview"
    BUSINESS_TERM_READ = "business_term.read"
    BUSINESS_TERM_MANAGE = "business_term.manage"
    # Module 10: SQL generation (untrusted candidate SQL; never executed by this module).
    AI_SQL_GENERATE = "ai.sql.generate"
    AI_SQL_VALIDATE = "ai.sql.validate"
    AI_SQL_SECURITY_AUDIT = "ai.sql.security.audit"
    # Module 12: execution of guard-approved SQL and its results.
    AI_SQL_EXECUTE = "ai.sql.execute"
    AI_SQL_EXECUTION_READ_ALL = "ai.sql.execution.read_all"
    AI_SQL_RESULT_EXPORT = "ai.sql.result.export"


@dataclass(frozen=True, slots=True)
class SystemPermissionDefinition:
    permission: Permission
    name: str
    description: str
    delegable: bool

    @property
    def key(self) -> str:
        return self.permission.value


SYSTEM_PERMISSIONS: tuple[SystemPermissionDefinition, ...] = (
    SystemPermissionDefinition(Permission.USERS_READ, "View users", "List and view user accounts.", True),
    SystemPermissionDefinition(
        Permission.USERS_MANAGE, "Manage users", "Create, edit, activate, deactivate and delete user accounts.", False
    ),
    SystemPermissionDefinition(Permission.SETTINGS_READ, "View settings", "Read application settings.", True),
    SystemPermissionDefinition(Permission.SETTINGS_MANAGE, "Manage settings", "Change application settings.", False),
    SystemPermissionDefinition(Permission.ROLES_READ, "View roles", "List and view roles.", True),
    SystemPermissionDefinition(
        Permission.ROLES_MANAGE, "Manage roles", "Create, edit and delete roles and change their permissions.", False
    ),
    SystemPermissionDefinition(Permission.PERMISSIONS_READ, "View permissions", "List and view permissions.", True),
    SystemPermissionDefinition(
        Permission.PERMISSIONS_MANAGE, "Manage permissions", "Create, edit and delete permission definitions.", False
    ),
    SystemPermissionDefinition(
        Permission.ACCESS_REVIEW,
        "Review user access",
        "View other users' roles and effective permissions and run authorization checks for them.",
        True,
    ),
    SystemPermissionDefinition(
        Permission.ACCESS_ASSIGN,
        "Assign access",
        "Assign and revoke roles and direct permissions of users.",
        False,
    ),
    # Schema registry permissions are delegable: an administrator can create a custom
    # role (for example a data steward) that maintains schema metadata without
    # being able to manage users, roles or permissions.
    SystemPermissionDefinition(
        Permission.SCHEMA_READ, "View schemas", "View registered schema sources, tables, columns and relationships.", True
    ),
    SystemPermissionDefinition(Permission.SCHEMA_CREATE, "Create schemas", "Register new schema sources.", True),
    SystemPermissionDefinition(
        Permission.SCHEMA_UPDATE,
        "Edit schemas",
        "Edit schema sources and add or edit their table and column metadata.",
        True,
    ),
    SystemPermissionDefinition(
        Permission.SCHEMA_DELETE, "Delete schemas", "Delete schema sources, tables and columns.", True
    ),
    SystemPermissionDefinition(
        Permission.SCHEMA_IMPORT, "Import schemas", "Import schema metadata from JSON into a schema source.", True
    ),
    SystemPermissionDefinition(
        Permission.SCHEMA_EXPORT, "Export schemas", "Export registered schema metadata as JSON.", True
    ),
    SystemPermissionDefinition(
        Permission.SCHEMA_RELATIONSHIP_MANAGE,
        "Manage schema relationships",
        "Create, edit and remove relationships between registered tables.",
        True,
    ),
    # Connector permissions are delegable so an administrator can create, for example, an
    # integration-engineer role. They never reveal stored credentials: no permission does.
    SystemPermissionDefinition(
        Permission.CONNECTOR_READ,
        "View connectors",
        "View external API connectors (masked configuration only), their health and audit history.",
        True,
    ),
    SystemPermissionDefinition(Permission.CONNECTOR_CREATE, "Create connectors", "Configure new external API connectors.", True),
    SystemPermissionDefinition(
        Permission.CONNECTOR_UPDATE,
        "Edit connectors",
        "Change external API connector settings and replace their credentials.",
        True,
    ),
    SystemPermissionDefinition(Permission.CONNECTOR_DELETE, "Delete connectors", "Delete external API connectors.", True),
    SystemPermissionDefinition(
        Permission.CONNECTOR_TEST,
        "Test connectors",
        "Run the fixed, system-generated connection test against an external API connector.",
        True,
    ),
    SystemPermissionDefinition(
        Permission.CONNECTOR_ACTIVATE, "Activate connectors", "Activate and deactivate external API connectors.", True
    ),
    # AI provider permissions are delegable. None of them reveals the provider's API key, and
    # none lets a user send their own text to the provider: the test prompt is fixed.
    SystemPermissionDefinition(
        Permission.AI_PROVIDER_READ,
        "View AI provider",
        "View the configured AI provider and model (never the API key) and check provider health.",
        True,
    ),
    SystemPermissionDefinition(
        Permission.AI_PROVIDER_TEST,
        "Test AI provider",
        "Run the fixed, system-generated test prompt against the AI provider (uses provider tokens).",
        True,
    ),
    SystemPermissionDefinition(
        Permission.AI_USAGE_READ,
        "View AI usage",
        "View AI request usage records: provider, model, status, latency and reported token counts.",
        True,
    ),
    # Question understanding is delegable. Interpreting a question never grants data access:
    # what a user may ask about still comes from their database_data permissions.
    SystemPermissionDefinition(
        Permission.AI_QUESTION_UNDERSTAND,
        "Ask business questions",
        "Submit business questions for AI interpretation (uses provider tokens; data access still follows data permissions).",
        True,
    ),
    SystemPermissionDefinition(
        Permission.AI_QUESTION_PREVIEW,
        "Preview question interpretation",
        "Use the interpretation preview to test how questions are understood, with diagnostics (own data permissions apply).",
        True,
    ),
    SystemPermissionDefinition(
        Permission.BUSINESS_TERM_READ, "View business terms", "View business terminology definitions.", True
    ),
    SystemPermissionDefinition(
        Permission.BUSINESS_TERM_MANAGE,
        "Manage business terms",
        "Create, edit, activate, deactivate and delete business terminology definitions.",
        True,
    ),
    # SQL generation is delegable. It produces untrusted candidate SQL only: it never executes
    # anything, and seeing the SQL text still needs a data permission with allow_sql_view.
    SystemPermissionDefinition(
        Permission.AI_SQL_GENERATE,
        "Generate SQL",
        "Generate candidate SQL from interpreted questions and view own generations (uses provider tokens; "
        "nothing is executed, and data permissions still apply).",
        True,
    ),
    # SQL security validation is delegable: it never widens access. The guard re-authorizes every
    # query with the user's current data permissions, and an approval is single-use and short-lived.
    SystemPermissionDefinition(
        Permission.AI_SQL_VALIDATE,
        "Validate SQL",
        "Run the SQL security guard on own generated SQL and manage own short-lived execution approvals "
        "(data permissions and row-level restrictions still apply).",
        True,
    ),
    # Detailed internal rejection reasons can name tables a user may not see: administrators only.
    SystemPermissionDefinition(
        Permission.AI_SQL_SECURITY_AUDIT,
        "Review SQL security decisions",
        "View detailed internal reasons of SQL security validations for security investigation.",
        False,
    ),
    # Executing is delegable: only an own, guard-approved, single-use approval can run, and the
    # guard re-checks the user's current data permissions immediately before anything is sent.
    SystemPermissionDefinition(
        Permission.AI_SQL_EXECUTE,
        "Run approved SQL",
        "Execute own guard-approved queries once through the company API connector and view own execution "
        "results (data permissions are checked again before results are shown).",
        True,
    ),
    # Seeing who ran what across the organisation is an oversight capability: administrators only.
    # It shows execution metadata, never other users' result rows or SQL.
    SystemPermissionDefinition(
        Permission.AI_SQL_EXECUTION_READ_ALL,
        "View all SQL executions",
        "View the execution history and metadata of every user (never their result rows or SQL text).",
        False,
    ),
    # Export is separate from viewing: it also needs allow_export on the data permission.
    SystemPermissionDefinition(
        Permission.AI_SQL_RESULT_EXPORT,
        "Export query results",
        "Download own query results as CSV when the data permissions also allow export.",
        True,
    ),
)

SYSTEM_PERMISSION_BY_KEY: dict[str, SystemPermissionDefinition] = {d.key: d for d in SYSTEM_PERMISSIONS}
DELEGABLE_SYSTEM_PERMISSIONS: frozenset[str] = frozenset(d.key for d in SYSTEM_PERMISSIONS if d.delegable)
ADMINISTRATOR_ONLY_PERMISSIONS: frozenset[str] = frozenset(d.key for d in SYSTEM_PERMISSIONS if not d.delegable)


class AuthorizationPolicy(Protocol):
    """Decides which system permissions a user holds.

    Asynchronous because the Module 04 implementation reads the database.
    Implementations must deny by default and must deny inactive or deleted users.
    """

    async def permissions_for(self, user: User) -> frozenset[Permission]: ...

    async def is_allowed(self, user: User, permission: Permission) -> bool: ...


def is_live(user: User) -> bool:
    return user.is_active and user.deleted_at is None


class BootstrapAuthorizationPolicy:
    """Installation administrator: everything. Everyone else: nothing.

    Kept as the fallback when no database is configured (the engine needs one).
    """

    async def permissions_for(self, user: User) -> frozenset[Permission]:
        if user.is_superuser and is_live(user):
            return frozenset(Permission)
        return frozenset()

    async def is_allowed(self, user: User, permission: Permission) -> bool:
        return permission in await self.permissions_for(user)
