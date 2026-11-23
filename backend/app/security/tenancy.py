"""Organization (tenant) scoping rules.

The organization a request acts on is derived from the authenticated identity.
A client-supplied organization id is honoured only for platform administrators.
"""

import uuid

from app.core.exceptions import PermissionDeniedError
from app.security.principal import Principal


def resolve_organization_scope(principal: Principal, requested: uuid.UUID | None = None) -> uuid.UUID:
    if requested is None or requested == principal.organization_id:
        return principal.organization_id
    if principal.is_platform_admin:
        return requested
    raise PermissionDeniedError("Access to another organization is not allowed.")


def can_access_organization(principal: Principal, organization_id: uuid.UUID) -> bool:
    return principal.is_platform_admin or principal.organization_id == organization_id
