import pytest

from app.core.config import Settings
from app.security.passwords import PasswordHasher, PasswordPolicy


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost_kib=1024, parallelism=1)


def test_hash_is_argon2id_and_never_plaintext(hasher: PasswordHasher) -> None:
    password = "Correct-Horse-42!"
    hashed = hasher.hash(password)
    assert hashed.startswith("$argon2id$")
    assert password not in hashed


def test_same_password_hashes_differently_each_time(hasher: PasswordHasher) -> None:
    assert hasher.hash("Correct-Horse-42!") != hasher.hash("Correct-Horse-42!")


def test_verify_accepts_correct_and_rejects_wrong_password(hasher: PasswordHasher) -> None:
    hashed = hasher.hash("Correct-Horse-42!")
    assert hasher.verify(hashed, "Correct-Horse-42!") is True
    assert hasher.verify(hashed, "correct-horse-42!") is False
    assert hasher.verify("not-a-hash", "Correct-Horse-42!") is False


def test_needs_rehash_when_parameters_change(hasher: PasswordHasher) -> None:
    weak = hasher.hash("Correct-Horse-42!")
    stronger = PasswordHasher(time_cost=2, memory_cost_kib=2048, parallelism=1)
    assert stronger.needs_rehash(weak) is True
    assert stronger.needs_rehash(stronger.hash("Correct-Horse-42!")) is False


def test_production_defaults_meet_owasp_baseline() -> None:
    settings = Settings(_env_file=None)
    assert settings.argon2_memory_cost_kib >= 19 * 1024
    assert settings.argon2_time_cost >= 2


@pytest.mark.parametrize(
    "password,fragment",
    [
        ("Short-1!", "at least 12 characters"),
        ("alllowercaseletters", "at least three of"),
        ("Password123!", "too common"),
        (" Leading-space-42", "whitespace"),
        ("x" * 100 + "Aa1!" + "y" * 30, "at most 128"),
        ("Jane.Doe-Secret-42", "email name"),
    ],
)
def test_policy_rejects_weak_passwords(password: str, fragment: str) -> None:
    problems = PasswordPolicy(min_length=12).violations(password, email="jane.doe@acme.io")
    assert any(fragment in problem for problem in problems), problems


def test_policy_accepts_strong_password() -> None:
    assert PasswordPolicy().violations("Correct-Horse-42!", email="jane@acme.io") == []
