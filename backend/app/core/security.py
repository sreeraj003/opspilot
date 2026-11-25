"""Token primitives: opaque random tokens and signed access tokens.

* Opaque tokens (refresh tokens, password-reset tokens) are 256 bits from
  ``secrets`` and are stored only as a SHA-256 digest. A digest without a salt
  is sufficient here because the token itself has full entropy: there is
  nothing to brute-force, unlike a password.
* Access tokens are HS256 JWTs signed with ``SECRET_KEY``. They carry only the
  user id and the session id; every request re-checks both against the
  database (see ``app.services.auth_service``), so revocation, logout and
  deactivation take effect immediately rather than at token expiry.
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings

logger = logging.getLogger(__name__)

OPAQUE_TOKEN_BYTES = 32
_JWT_ALGORITHM = "HS256"
_ISSUER = "opspilot"
_AUDIENCE = "opspilot-api"
_ACCESS_TYPE = "access"
_LEEWAY_SECONDS = 5


def generate_opaque_token() -> str:
    """A URL-safe, cryptographically random token (256 bits)."""
    return secrets.token_urlsafe(OPAQUE_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hex SHA-256 digest of an opaque token; the only form ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(first: str, second: str) -> bool:
    return hmac.compare_digest(first.encode(), second.encode())


class InvalidAccessTokenError(Exception):
    """The token is malformed, has a bad signature, is expired or is not an access token."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    expires_at: datetime
    expires_in: int


class AccessTokenService:
    """Issues and verifies access tokens. One instance per application."""

    def __init__(self, signing_key: str, lifetime: timedelta) -> None:
        self._key = signing_key
        self._lifetime = lifetime

    @classmethod
    def from_settings(cls, settings: Settings) -> "AccessTokenService":
        if settings.secret_key is not None:
            key = settings.secret_key.get_secret_value()
        else:
            # Production refuses to start without SECRET_KEY (see Settings). In
            # development a per-process key is acceptable: a restart only forces
            # browsers to refresh their access token (refresh tokens are not JWTs).
            key = secrets.token_urlsafe(48)
            logger.warning("SECRET_KEY is not set; using a temporary signing key for this process")
        return cls(key, timedelta(minutes=settings.access_token_expire_minutes))

    def issue(self, user_id: uuid.UUID, session_id: uuid.UUID, *, now: datetime | None = None) -> IssuedAccessToken:
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self._lifetime
        payload = {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "typ": _ACCESS_TYPE,
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": uuid.uuid4().hex,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, self._key, algorithm=_JWT_ALGORITHM)
        return IssuedAccessToken(token=token, expires_at=expires_at, expires_in=int(self._lifetime.total_seconds()))

    def verify(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._key,
                # An explicit allow-list: "none" and asymmetric algorithms are never accepted.
                algorithms=[_JWT_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "sid", "typ", "iss", "aud"]},
            )
            if payload.get("typ") != _ACCESS_TYPE:
                raise InvalidAccessTokenError("not an access token")
            return AccessTokenClaims(
                user_id=uuid.UUID(str(payload["sub"])),
                session_id=uuid.UUID(str(payload["sid"])),
                expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise InvalidAccessTokenError(type(exc).__name__) from None
