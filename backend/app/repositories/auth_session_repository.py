"""Data access for login sessions and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession, RefreshToken


class AuthSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_session(self, auth_session: AuthSession) -> AuthSession:
        self._session.add(auth_session)
        self._session.flush()
        return auth_session

    def get_session(self, session_id: uuid.UUID) -> AuthSession | None:
        return self._session.get(AuthSession, session_id)

    def add_refresh_token(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        self._session.flush()
        return token

    def get_refresh_token_by_hash(self, token_hash: str, *, for_update: bool = False) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            # Serializes concurrent refreshes of the same token on PostgreSQL.
            statement = statement.with_for_update(of=RefreshToken)
        return self._session.scalar(statement)

    def revoke_session(self, auth_session: AuthSession, *, reason: str, at: datetime) -> None:
        if auth_session.revoked_at is None:
            auth_session.revoked_at = at
            auth_session.revoked_reason = reason
        self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        self._session.flush()

    def revoke_all_for_user(self, user_id: uuid.UUID, *, reason: str, at: datetime) -> int:
        sessions = list(
            self._session.scalars(
                select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            )
        )
        for auth_session in sessions:
            self.revoke_session(auth_session, reason=reason, at=at)
        return len(sessions)
