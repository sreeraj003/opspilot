"""One definition of a valid, normalised email address.

Normalisation: surrounding whitespace removed, syntax validated (no DNS
lookup: installations may run without internet access), domain IDNA
normalised, and the whole address lower-cased. Lower-casing the local part is
a deliberate choice: virtually every real mail system treats it
case-insensitively, and it prevents look-alike duplicate accounts.
"""

from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, Field

from app.models.user import EMAIL_MAX_LENGTH


class InvalidEmailError(ValueError):
    pass


def normalize_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidEmailError("Enter a valid email address.") from exc
    normalized = result.normalized.lower()
    if len(normalized) > EMAIL_MAX_LENGTH:
        raise InvalidEmailError("Email address is too long.")
    return normalized


def try_normalize_email(value: str) -> str | None:
    """Normalised email, or ``None`` if invalid (for login, which must not say why it failed)."""
    try:
        return normalize_email(value)
    except InvalidEmailError:
        return None


def _validate(value: str) -> str:
    return normalize_email(value)


EmailAddress = Annotated[str, Field(max_length=EMAIL_MAX_LENGTH + 10), AfterValidator(_validate)]
