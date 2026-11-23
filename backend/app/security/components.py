"""Process-wide security components, built once per application."""

from dataclasses import dataclass

from app.core.config import Settings
from app.security.passwords import PasswordHasher, PasswordPolicy
from app.security.rate_limit import InMemoryRateLimiter, RateLimiter
from app.security.tokens import TokenService


@dataclass
class SecurityComponents:
    password_hasher: PasswordHasher
    password_policy: PasswordPolicy
    token_service: TokenService
    rate_limiter: RateLimiter

    @classmethod
    def from_settings(cls, settings: Settings) -> "SecurityComponents":
        return cls(
            password_hasher=PasswordHasher.from_settings(settings),
            password_policy=PasswordPolicy.from_settings(settings),
            token_service=TokenService.from_settings(settings),
            rate_limiter=InMemoryRateLimiter(),
        )
