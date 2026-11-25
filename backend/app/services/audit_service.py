"""Records authentication and account security events.

Events are written in the caller's transaction, so an event exists exactly
when the action it describes was committed. A structured log line is emitted
as well. Neither ever contains passwords, tokens or other credentials:
``details`` values are restricted to simple scalars and secret-looking keys
are refused.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import is_sensitive_key
from app.models.auth_event import AuthEvent

logger = logging.getLogger("app.security")

_Scalar = str | int | float | bool | None


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        event_type: str,
        *,
        user_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        details: dict[str, _Scalar] | None = None,
    ) -> AuthEvent:
        safe_details = _safe_details(details or {})
        event = AuthEvent(
            event_type=event_type,
            user_id=user_id,
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            details=safe_details,
        )
        self._session.add(event)
        await self._session.flush()
        logger.info(
            "Security event",
            extra={
                "event_type": event_type,
                "user_id": str(user_id) if user_id else None,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                **{f"detail_{key}": value for key, value in safe_details.items()},
            },
        )
        return event


def _safe_details(details: dict[str, Any]) -> dict[str, _Scalar]:
    safe: dict[str, _Scalar] = {}
    for key, value in details.items():
        if is_sensitive_key(key):
            raise ValueError(f"audit detail {key!r} looks like a credential and must not be recorded")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise TypeError(f"audit detail {key!r} must be a simple value")
        safe[key] = value[:200] if isinstance(value, str) else value
    return safe
