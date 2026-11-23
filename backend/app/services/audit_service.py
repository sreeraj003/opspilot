"""Records security-sensitive actions to the audit trail."""

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.request_context import get_request_id
from app.models.audit import AuditEvent
from app.repositories.audit_repository import AuditRepository

logger = logging.getLogger("app.audit")


class AuditAction(StrEnum):
    REGISTER = "auth.register"
    LOGIN = "auth.login"
    TOKEN_REFRESH = "auth.token_refresh"
    REFRESH_TOKEN_REUSE = "auth.refresh_token_reuse_detected"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    PERMISSION_DENIED = "authz.permission_denied"
    USER_CREATE = "users.create"
    USER_ROLES_UPDATE = "users.roles_update"
    USER_ACTIVATE = "users.activate"
    USER_DEACTIVATE = "users.deactivate"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


@dataclass(frozen=True)
class ClientInfo:
    ip_address: str | None = None
    user_agent: str | None = None


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    def record(
        self,
        action: AuditAction,
        outcome: AuditOutcome,
        *,
        client: ClientInfo,
        organization_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Add an audit event to the current transaction.

        Callers that are about to fail the request must commit before raising,
        otherwise the event is rolled back with the rest of the transaction.
        Details must never include secrets (passwords, tokens).
        """
        event = self._repository.add(
            AuditEvent(
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                action=action.value,
                outcome=outcome.value,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                ip_address=client.ip_address,
                user_agent=client.user_agent,
                request_id=get_request_id(),
                details=details or {},
            )
        )
        logger.info(
            "audit event",
            extra={
                "audit_action": action.value,
                "audit_outcome": outcome.value,
                "organization_id": str(organization_id) if organization_id else None,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
            },
        )
        return event
