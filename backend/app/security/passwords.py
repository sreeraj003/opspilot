"""Password hashing (Argon2id) and password policy."""

import re
from dataclasses import dataclass

from argon2 import PasswordHasher as Argon2Hasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

MAX_PASSWORD_LENGTH = 128  # bounds hashing cost per request

# A short deny-list of the most common passwords that would otherwise satisfy the
# length and character-class rules.
_COMMON_PASSWORDS = frozenset(
    {
        "password123!",
        "password1234",
        "p@ssw0rd1234",
        "p@ssword1234",
        "qwerty123456",
        "qwertyuiop12",
        "welcome12345",
        "welcome@1234",
        "letmein12345",
        "administrator",
        "admin@123456",
        "changeme1234",
        "iloveyou1234",
        "123456789abc",
        "abc123456789",
    }
)


class PasswordHasher:
    """Argon2id hashing with parameters from configuration."""

    def __init__(self, time_cost: int, memory_cost_kib: int, parallelism: int) -> None:
        self._hasher = Argon2Hasher(
            time_cost=time_cost, memory_cost=memory_cost_kib, parallelism=parallelism, type=Type.ID
        )
        # Verified against when the account does not exist, so a login for an
        # unknown email costs the same as one for a real account.
        self._dummy_hash = self._hasher.hash("opspilot-timing-equalizer")

    @classmethod
    def from_settings(cls, settings: Settings) -> "PasswordHasher":
        return cls(settings.argon2_time_cost, settings.argon2_memory_cost_kib, settings.argon2_parallelism)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def verify_dummy(self, password: str) -> None:
        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


@dataclass(frozen=True)
class PasswordPolicy:
    min_length: int = 12
    max_length: int = MAX_PASSWORD_LENGTH
    min_character_classes: int = 3

    @classmethod
    def from_settings(cls, settings: Settings) -> "PasswordPolicy":
        return cls(min_length=settings.password_min_length)

    def violations(self, password: str, *, email: str | None = None) -> list[str]:
        """Return human-readable rule violations; an empty list means the password is acceptable."""
        problems: list[str] = []
        if len(password) < self.min_length:
            problems.append(f"Password must be at least {self.min_length} characters long.")
        if len(password) > self.max_length:
            problems.append(f"Password must be at most {self.max_length} characters long.")
        classes = sum(bool(re.search(pattern, password)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
        if classes < self.min_character_classes:
            problems.append(
                "Password must contain at least three of: lowercase letters, uppercase letters, digits and symbols."
            )
        if password != password.strip():
            problems.append("Password must not start or end with whitespace.")
        if password.lower() in _COMMON_PASSWORDS:
            problems.append("Password is too common.")
        if email:
            local_part = email.split("@", 1)[0].lower()
            if len(local_part) >= 4 and local_part in password.lower():
                problems.append("Password must not contain your email name.")
        return problems
