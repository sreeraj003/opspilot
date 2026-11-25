"""Password hashing and policy (no database)."""

import pytest

from app.services.password_service import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    PasswordService,
    password_policy_problem,
)


@pytest.fixture(scope="module")
def service() -> PasswordService:
    return PasswordService()  # production parameters


def test_hashes_with_argon2id_and_production_parameters(service: PasswordService) -> None:
    digest = service.hash("correct horse battery staple")

    assert digest.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert "correct horse" not in digest
    assert service.hash("correct horse battery staple") != digest  # salted
    assert not service.needs_rehash(digest)


def test_verifies_correct_and_rejects_wrong_passwords(service: PasswordService) -> None:
    digest = service.hash("correct horse battery staple")

    assert service.verify(digest, "correct horse battery staple")
    assert not service.verify(digest, "correct horse battery stapl")
    assert not service.verify(digest, "Correct horse battery staple")
    assert not service.verify("not-a-hash", "correct horse battery staple")
    assert not service.verify(None, "anything at all here")


def test_unicode_is_normalised_consistently(service: PasswordService) -> None:
    composed = "café au lait s'il vous plaît"
    decomposed = "café au lait s'il vous plâit".replace("̂i", "î")
    digest = service.hash(composed)

    assert service.verify(digest, decomposed)


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("x" * (MIN_PASSWORD_LENGTH - 1), "at least"),
        ("x" * (MAX_PASSWORD_LENGTH + 1), "at most"),
        (" " * 20, "only spaces"),
        ("Password1234", "too common"),
        ("abababababababab", "too common"),
    ],
)
def test_policy_rejections(password: str, fragment: str) -> None:
    problem = password_policy_problem(password)

    assert problem is not None and fragment in problem


def test_policy_accepts_passphrases_without_composition_rules() -> None:
    for password in ("all lower case words here", "x" * MIN_PASSWORD_LENGTH + "yz", "😀 emoji passphrase works", "a" * 50 + "b" * 50 + "c"):
        assert password_policy_problem(password) is None, password


def test_policy_rejects_passwords_built_from_the_account_identity() -> None:
    assert password_policy_problem("grace.hopper2026", identifiers=("grace.hopper@example.com",)) is not None
    assert password_policy_problem("ghopper-ghopper", identifiers=(None, "ghopper")) is not None
    # A long passphrase that merely contains the name is fine.
    assert password_policy_problem("grace likes very long passphrases", identifiers=("grace@example.com",)) is None


def test_hash_enforces_the_policy(service: PasswordService) -> None:
    with pytest.raises(PasswordPolicyError):
        service.hash("too short")
