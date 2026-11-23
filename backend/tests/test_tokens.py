import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.exceptions import AuthenticationError
from app.security.tokens import TokenService, generate_refresh_token, hash_refresh_token
from tests.helpers import TEST_JWT_SECRET

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _service(clock=lambda: NOW, **overrides) -> TokenService:
    values = {
        "secret_key": TEST_JWT_SECRET,
        "algorithm": "HS256",
        "issuer": "opspilot-api",
        "audience": "opspilot",
        "access_token_ttl": timedelta(minutes=15),
        "clock": clock,
    }
    values.update(overrides)
    return TokenService(**values)


def _ids() -> dict[str, uuid.UUID]:
    return {"user_id": uuid.uuid4(), "organization_id": uuid.uuid4(), "session_id": uuid.uuid4()}


def test_round_trip_carries_identity_only() -> None:
    ids = _ids()
    issued = _service().create_access_token(**ids)
    claims = _service().decode_access_token(issued.token)
    assert (claims.user_id, claims.organization_id, claims.session_id) == tuple(ids.values())
    assert issued.expires_in == 900
    payload = jwt.decode(issued.token, options={"verify_signature": False})
    assert "permissions" not in payload and "roles" not in payload


def test_expired_token_is_rejected() -> None:
    token = _service().create_access_token(**_ids()).token
    later = _service(clock=lambda: NOW + timedelta(minutes=15, seconds=1))
    with pytest.raises(AuthenticationError):
        later.decode_access_token(token)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
        lambda t: jwt.encode(jwt.decode(t, options={"verify_signature": False}), "x" * 40, algorithm="HS256"),
        lambda t: jwt.encode(
            {**jwt.decode(t, options={"verify_signature": False}), "aud": "other"},
            TEST_JWT_SECRET,
            algorithm="HS256",
        ),
        lambda t: jwt.encode(
            {**jwt.decode(t, options={"verify_signature": False}), "typ": "refresh"},
            TEST_JWT_SECRET,
            algorithm="HS256",
        ),
        lambda t: jwt.encode(jwt.decode(t, options={"verify_signature": False}), None, algorithm="none"),
        lambda t: "not-a-jwt",
    ],
    ids=["bad-signature", "wrong-key", "wrong-audience", "wrong-type", "alg-none", "garbage"],
)
def test_tampered_tokens_are_rejected(tamper) -> None:
    token = _service().create_access_token(**_ids()).token
    with pytest.raises(AuthenticationError):
        _service().decode_access_token(tamper(token))


def test_refresh_tokens_are_random_and_hashed() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()
    assert first != second and len(first) >= 64
    digest = hash_refresh_token(first)
    assert len(digest) == 64 and first not in digest
    assert digest == hash_refresh_token(first)
