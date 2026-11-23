"""Permission and role catalog (single source of truth).

Application code checks *permissions*; role codes appear only here and in the
migration that seeds them. ``tests/test_rbac_catalog.py`` verifies that the
seeded database matches this catalog.
"""

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    USERS_READ = "users.read"
    USERS_CREATE = "users.create"
    USERS_UPDATE = "users.update"
    USERS_DELETE = "users.delete"
    CUSTOMERS_READ = "customers.read"
    CUSTOMERS_CREATE = "customers.create"
    CUSTOMERS_UPDATE = "customers.update"
    TICKETS_READ = "tickets.read"
    TICKETS_MANAGE = "tickets.manage"
    INCIDENTS_READ = "incidents.read"
    INCIDENTS_MANAGE = "incidents.manage"
    DOCUMENTS_UPLOAD = "documents.upload"
    DOCUMENTS_READ = "documents.read"
    ANALYTICS_READ = "analytics.read"
    WORKFLOWS_MANAGE = "workflows.manage"
    WORKFLOWS_EXECUTE = "workflows.execute"
    AUDIT_READ = "audit.read"
    # Grants access across organizations. Only platform-level administrators hold it.
    PLATFORM_ADMIN = "platform.admin"


PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.USERS_READ: "View users in the organization",
    Permission.USERS_CREATE: "Create users in the organization",
    Permission.USERS_UPDATE: "Update users, their roles and activation state",
    Permission.USERS_DELETE: "Delete users",
    Permission.CUSTOMERS_READ: "View customers",
    Permission.CUSTOMERS_CREATE: "Create customers",
    Permission.CUSTOMERS_UPDATE: "Update customers",
    Permission.TICKETS_READ: "View support tickets",
    Permission.TICKETS_MANAGE: "Create, update and resolve support tickets",
    Permission.INCIDENTS_READ: "View incidents",
    Permission.INCIDENTS_MANAGE: "Create, update and resolve incidents",
    Permission.DOCUMENTS_UPLOAD: "Upload documents to the knowledge base",
    Permission.DOCUMENTS_READ: "Read and search documents",
    Permission.ANALYTICS_READ: "View analytics and business metrics",
    Permission.WORKFLOWS_MANAGE: "Define and manage workflows",
    Permission.WORKFLOWS_EXECUTE: "Run workflows",
    Permission.AUDIT_READ: "View the audit log",
    Permission.PLATFORM_ADMIN: "Administer the platform across all organizations",
}


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    name: str
    description: str
    permissions: frozenset[Permission]


_ALL = frozenset(Permission)
_READ_CORE = frozenset(
    {
        Permission.CUSTOMERS_READ,
        Permission.TICKETS_READ,
        Permission.INCIDENTS_READ,
        Permission.DOCUMENTS_READ,
        Permission.ANALYTICS_READ,
    }
)

ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition("super_admin", "Super Admin", "Platform-level administrator across all organizations", _ALL),
    RoleDefinition(
        "org_admin",
        "Organization Admin",
        "Full administration of a single organization",
        _ALL - {Permission.PLATFORM_ADMIN},
    ),
    RoleDefinition(
        "operations_manager",
        "Operations Manager",
        "Runs day-to-day operations, workflows and incident response",
        _READ_CORE
        | {
            Permission.USERS_READ,
            Permission.CUSTOMERS_CREATE,
            Permission.CUSTOMERS_UPDATE,
            Permission.TICKETS_MANAGE,
            Permission.INCIDENTS_MANAGE,
            Permission.DOCUMENTS_UPLOAD,
            Permission.WORKFLOWS_MANAGE,
            Permission.WORKFLOWS_EXECUTE,
            Permission.AUDIT_READ,
        },
    ),
    RoleDefinition(
        "support_agent",
        "Support Agent",
        "Handles customer support tickets",
        frozenset(
            {
                Permission.CUSTOMERS_READ,
                Permission.CUSTOMERS_UPDATE,
                Permission.TICKETS_READ,
                Permission.TICKETS_MANAGE,
                Permission.INCIDENTS_READ,
                Permission.DOCUMENTS_READ,
                Permission.WORKFLOWS_EXECUTE,
            }
        ),
    ),
    RoleDefinition("analyst", "Analyst", "Explores business data and analytics", _READ_CORE),
    RoleDefinition(
        "developer",
        "Developer",
        "Investigates incidents and maintains technical documentation",
        frozenset(
            {
                Permission.TICKETS_READ,
                Permission.INCIDENTS_READ,
                Permission.INCIDENTS_MANAGE,
                Permission.DOCUMENTS_READ,
                Permission.DOCUMENTS_UPLOAD,
                Permission.ANALYTICS_READ,
                Permission.WORKFLOWS_EXECUTE,
            }
        ),
    ),
    RoleDefinition(
        "viewer",
        "Viewer",
        "Read-only access to operational data",
        frozenset(
            {
                Permission.CUSTOMERS_READ,
                Permission.TICKETS_READ,
                Permission.INCIDENTS_READ,
                Permission.DOCUMENTS_READ,
            }
        ),
    ),
)

ROLES_BY_CODE: dict[str, RoleDefinition] = {role.code: role for role in ROLES}

# Role given to the first user of an organization created through self-service registration.
ORGANIZATION_OWNER_ROLE = "org_admin"
# Role given to a platform administrator bootstrapped from the command line.
PLATFORM_ADMIN_ROLE = "super_admin"
