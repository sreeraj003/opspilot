"""JWT access tokens and opaque refresh tokens."""

import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.utils.time import utc_now

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    token_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    expires_at: datetime
    expires_in: int


class TokenService:
    """Creates and validates signed access tokens.

    Access tokens carry identity only (user, organization, session). Roles and
    permissions are loaded from the database on every request, so changes take
    effect immediately rather than when the token expires.
    """

    def __init__(
        self,
        *,
        secret_key: str,
        algorithm: str,
        issuer: str,
        audience: str,
        access_token_ttl: timedelta,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._issuer = issuer
        self._audience = audience
        self._ttl = access_token_ttl
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings, clock: Callable[[], datetime] = utc_now) -> "TokenService":
        assert settings.jwt_secret_key is not None  # guaranteed by Settings validation
        return cls(
            secret_key=settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            access_token_ttl=timedelta(minutes=settings.access_token_expire_minutes),
            clock=clock,
        )

    def create_access_token(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, session_id: uuid.UUID
    ) -> IssuedAccessToken:
        issued_at = self._clock()
        expires_at = issued_at + self._ttl
        payload = {
            "sub": str(user_id),
            "org": str(organization_id),
            "sid": str(session_id),
            "jti": uuid.uuid4().hex,
            "typ": ACCESS_TOKEN_TYPE,
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(issued_at.timestamp()),
            "nbf": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, self._secret_key, algorithm=self._algorithm)
        return IssuedAccessToken(token=token, expires_at=expires_at, expires_in=int(self._ttl.total_seconds()))

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        """Validate signature, algorithm, issuer, audience, type and expiry.

        Every failure raises the same generic ``AuthenticationError``.
        """
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                # Time claims are checked below against the injected clock, so
                # issuance and validation always agree on "now".
                options={
                    "require": ["sub", "org", "sid", "jti", "typ", "iat", "exp"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
            now = self._clock().timestamp()
            if not isinstance(payload["exp"], int) or payload["exp"] <= now:
                raise jwt.ExpiredSignatureError
            if isinstance(payload.get("nbf"), int) and payload["nbf"] > now + 5:
                raise jwt.ImmatureSignatureError
            if payload["typ"] != ACCESS_TOKEN_TYPE:
                raise jwt.InvalidTokenError
            return AccessTokenClaims(
                user_id=uuid.UUID(payload["sub"]),
                organization_id=uuid.UUID(payload["org"]),
                session_id=uuid.UUID(payload["sid"]),
                token_id=str(payload["jti"]),
                issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise AuthenticationError("Invalid or expired access token.") from exc


def generate_refresh_token() -> str:
    """A 384-bit random opaque token. Only its digest is ever stored."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 digest. Refresh tokens are high-entropy, so a fast hash is sufficient."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
