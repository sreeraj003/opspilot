"""Central password hashing and password policy.

* Hashing uses Argon2id through ``argon2-cffi`` (RFC 9106 low-memory profile:
  t=3, m=64 MiB, p=4). The parameters are stored inside each hash, so they can
  be raised later; ``needs_rehash`` reports hashes made with older parameters
  and login upgrades them transparently.
* Policy follows NIST SP 800-63B: a minimum length, generous maximum, any
  characters (spaces, Unicode, pasted passphrases), no composition rules, and
  rejection of well-known passwords and passwords built from the account's own
  identifiers. Passwords are never truncated: anything over the maximum is
  rejected with a clear message instead.
"""

import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.profiles import RFC_9106_LOW_MEMORY

from app.services.common_passwords import COMMON_PASSWORDS

MIN_PASSWORD_LENGTH = 12
# Long enough for any passphrase, short enough that hashing cannot be abused
# for denial of service.
MAX_PASSWORD_LENGTH = 256


class PasswordPolicyError(ValueError):
    """The password does not meet the policy. The message is safe to show to users."""


def _normalize(password: str) -> str:
    # NFKC so the same passphrase typed on different keyboards/OSes hashes the same.
    return unicodedata.normalize("NFKC", password)


def password_policy_problem(password: str, *, identifiers: tuple[str | None, ...] = ()) -> str | None:
    """Why ``password`` is not acceptable, or ``None`` if it is."""
    normalized = _normalize(password)
    length = len(normalized)
    if length < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if length > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    if not normalized.strip():
        return "Password must not be only spaces."
    folded = normalized.casefold()
    if folded in COMMON_PASSWORDS or len(set(folded)) <= 2:
        return "This password is too common or too easy to guess. Choose a different one."
    for identifier in identifiers:
        if not identifier:
            continue
        candidates = {identifier.casefold(), identifier.split("@", 1)[0].casefold()}
        for candidate in candidates:
            if len(candidate) < 4 or candidate not in folded:
                continue
            # What is left once the identifier is removed must be substantial on its own.
            remainder = "".join(ch for ch in folded.replace(candidate, "") if ch.isalnum())
            if len(remainder) < 8:
                return "Password must not be based on your email address, username or name."
    return None


class PasswordService:
    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)
        # Verified against when the account does not exist, so a login for an
        # unknown email costs the same time as one for a real account.
        self._dummy_hash = self._hasher.hash("opspilot-timing-equalizer-not-a-password")

    def validate(self, password: str, *, identifiers: tuple[str | None, ...] = ()) -> None:
        problem = password_policy_problem(password, identifiers=identifiers)
        if problem is not None:
            raise PasswordPolicyError(problem)

    def hash(self, password: str, *, identifiers: tuple[str | None, ...] = ()) -> str:
        """Validate against the policy, then hash. Raises ``PasswordPolicyError``."""
        self.validate(password, identifiers=identifiers)
        return self._hasher.hash(_normalize(password))

    def hash_unchecked(self, password: str) -> str:
        """Hash without the policy check: only for re-hashing an already accepted password."""
        return self._hasher.hash(_normalize(password))

    def verify(self, password_hash: str | None, password: str) -> bool:
        if len(password) > MAX_PASSWORD_LENGTH * 4:
            # Refuse absurd inputs before doing any hashing work.
            self._burn()
            return False
        if not password_hash:
            self._burn()
            return False
        try:
            return self._hasher.verify(password_hash, _normalize(password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    def burn_time(self) -> None:
        """Spend the same time as one verification (used for unknown accounts)."""
        self._burn()

    def _burn(self) -> None:
        try:
            self._hasher.verify(self._dummy_hash, "opspilot-wrong-password")
        except VerificationError:
            pass
