"""Administrator user management against PostgreSQL."""

import uuid

import pytest

from tests.auth.conftest import ADMIN_EMAIL, PASSWORD, Api

pytestmark = pytest.mark.postgres

USERS = "/api/v1/users"
NEW_USER = {"email": "Grace@Example.com", "full_name": "Grace Hopper", "password": "a long enough passphrase"}
SAFE_FIELDS = {
    "id",
    "email",
    "username",
    "full_name",
    "is_active",
    "is_verified",
    "is_superuser",
    "is_locked",
    "locked_until",
    "last_login_at",
    "created_at",
    "updated_at",
}


def _create(api: Api, token: str, **overrides: object) -> dict:
    response = api.client.post(USERS, json={**NEW_USER, **overrides}, headers=api.bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_administrator_creates_user(api: Api, admin_token: str) -> None:
    body = _create(api, admin_token, username="grace")

    assert set(body) == SAFE_FIELDS
    assert body["email"] == "grace@example.com"
    assert body["username"] == "grace"
    assert body["is_active"] is True
    assert body["is_superuser"] is False
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 200
    stored = api.sql("SELECT password_hash FROM users WHERE email = 'grace@example.com'")[0][0]
    assert stored.startswith("$argon2id$")
    assert len(api.events("account_created")) == 1


def test_created_user_can_start_inactive(api: Api, admin_token: str) -> None:
    body = _create(api, admin_token, is_active=False)

    assert body["is_active"] is False
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 401


def test_duplicate_email_is_rejected_case_insensitively(api: Api, admin_token: str) -> None:
    _create(api, admin_token, username="grace")

    duplicate_email = api.client.post(USERS, json={**NEW_USER, "email": "GRACE@example.COM"}, headers=api.bearer(admin_token))
    duplicate_username = api.client.post(
        USERS, json={**NEW_USER, "email": "other@example.com", "username": "GRACE"}, headers=api.bearer(admin_token)
    )

    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["error"]["code"] == "CONFLICT"
    assert duplicate_username.status_code == 409


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "spaces in@example.com", "@example.com", ""])
def test_invalid_email_is_rejected(api: Api, admin_token: str, email: str) -> None:
    response = api.client.post(USERS, json={**NEW_USER, "email": email}, headers=api.bearer(admin_token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 12"),
        ("password1234", "too common"),
        ("aaaaaaaaaaaaaaaa", "too common"),
        ("grace@example.com1", "email address"),
        ("x" * 257, "at most 256"),
    ],
)
def test_weak_passwords_are_rejected(api: Api, admin_token: str, password: str, message: str) -> None:
    response = api.client.post(USERS, json={**NEW_USER, "password": password}, headers=api.bearer(admin_token))

    assert response.status_code == 422
    assert message in response.json()["error"]["message"]
    assert password not in response.text


def test_long_passphrases_with_spaces_and_unicode_are_accepted(api: Api, admin_token: str) -> None:
    passphrase = "naïve café ☕ " * 10  # 130 characters
    _create(api, admin_token, password=passphrase)

    assert api.login("grace@example.com", passphrase).status_code == 200
    assert api.login("grace@example.com", passphrase[:-1]).status_code == 401  # never truncated or trimmed


def test_administrator_lists_users_with_pagination(api: Api, admin_token: str) -> None:
    for index in range(5):
        _create(api, admin_token, email=f"user{index}@example.com", full_name=f"User {index}")

    first = api.client.get(f"{USERS}?limit=2&sort=email&order=asc", headers=api.bearer(admin_token)).json()
    third = api.client.get(f"{USERS}?limit=2&offset=4&sort=email&order=asc", headers=api.bearer(admin_token)).json()

    assert (first["total"], first["offset"], first["limit"]) == (6, 0, 2)  # 5 users + the administrator
    assert [item["email"] for item in first["items"]] == ["admin@example.com", "user0@example.com"]
    assert [item["email"] for item in third["items"]] == ["user3@example.com", "user4@example.com"]
    for item in first["items"]:
        assert set(item) == SAFE_FIELDS
    assert api.client.get(f"{USERS}?limit=101", headers=api.bearer(admin_token)).status_code == 422
    assert api.client.get(f"{USERS}?sort=password_hash", headers=api.bearer(admin_token)).status_code == 422


def test_search_and_status_filter(api: Api, admin_token: str) -> None:
    _create(api, admin_token, email="grace@example.com", full_name="Grace Hopper", username="ghopper")
    inactive = _create(api, admin_token, email="alan@example.com", full_name="Alan Turing", is_active=False)
    _create(api, admin_token, email="percent@example.com", full_name="Fifty % Done")

    def emails(query: str) -> list[str]:
        response = api.client.get(f"{USERS}?{query}", headers=api.bearer(admin_token))
        assert response.status_code == 200, response.text
        return sorted(item["email"] for item in response.json()["items"])

    assert emails("search=HOPPER") == ["grace@example.com"]
    assert emails("search=ghop") == ["grace@example.com"]
    assert emails("search=alan%40") == ["alan@example.com"]
    assert emails("search=%25") == ["percent@example.com"]  # LIKE wildcards are matched literally
    assert emails("status=inactive") == [inactive["email"]]
    assert "alan@example.com" not in emails("status=active")
    assert emails("search=nobody") == []


def test_get_user_details_and_sessions(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)
    api.login("grace@example.com", NEW_USER["password"])

    detail = api.client.get(f"{USERS}/{created['id']}", headers=api.bearer(admin_token))
    sessions = api.client.get(f"{USERS}/{created['id']}/sessions", headers=api.bearer(admin_token))
    missing = api.client.get(f"{USERS}/{uuid.uuid4()}", headers=api.bearer(admin_token))

    assert detail.status_code == 200
    assert detail.json()["active_session_count"] == 1
    assert detail.json()["last_login_at"] is not None
    assert sessions.status_code == 200
    assert set(sessions.json()[0]) == {"id", "created_at", "last_used_at", "expires_at", "user_agent", "ip_address"}
    assert missing.status_code == 404


def test_update_user(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)

    response = api.client.patch(
        f"{USERS}/{created['id']}",
        json={"full_name": "  Rear Admiral   Grace Hopper ", "email": "G.Hopper@Example.com", "username": "admiral"},
        headers=api.bearer(admin_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["full_name"] == "Rear Admiral Grace Hopper"
    assert body["email"] == "g.hopper@example.com"
    assert body["username"] == "admiral"
    assert api.login("g.hopper@example.com", NEW_USER["password"]).status_code == 200
    assert api.client.patch(f"{USERS}/{created['id']}", json={"email": ADMIN_EMAIL}, headers=api.bearer(admin_token)).status_code == 409
    assert api.client.patch(f"{USERS}/{created['id']}", json={"email": None}, headers=api.bearer(admin_token)).status_code == 422


def test_deactivate_and_activate_user(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)
    access = api.token_for("grace@example.com", NEW_USER["password"])

    deactivated = api.client.post(f"{USERS}/{created['id']}/deactivate", headers=api.bearer(admin_token))

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert api.client.get("/api/v1/auth/me", headers=api.bearer(access)).status_code == 401
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 401
    assert api.sql("SELECT revoked_reason FROM user_sessions WHERE revoked_at IS NOT NULL") == [("user_deactivated",)]
    # History is preserved: the account and its audit trail still exist.
    assert len(api.events("account_deactivated")) == 1

    activated = api.client.post(f"{USERS}/{created['id']}/activate", headers=api.bearer(admin_token))

    assert activated.status_code == 200
    assert activated.json()["is_active"] is True
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 200


def test_activation_clears_a_lockout(api_factory) -> None:  # type: ignore[no-untyped-def]
    api = api_factory(login_max_failed_attempts=1)
    api.create_user(ADMIN_EMAIL, superuser=True)
    admin = api.token_for(ADMIN_EMAIL)
    user_id = api.create_user("grace@example.com")
    api.login("grace@example.com", "wrong password entirely")
    listed = api.client.get(f"{USERS}?status=locked", headers=api.bearer(admin)).json()
    assert [item["email"] for item in listed["items"]] == ["grace@example.com"]
    assert listed["items"][0]["is_locked"] is True

    api.client.post(f"{USERS}/{user_id}/activate", headers=api.bearer(admin))

    assert api.login("grace@example.com").status_code == 200


def test_revoke_user_sessions(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)
    first = api.token_for("grace@example.com", NEW_USER["password"])
    second = api.token_for("grace@example.com", NEW_USER["password"])

    response = api.client.post(f"{USERS}/{created['id']}/revoke-sessions", headers=api.bearer(admin_token))

    assert response.status_code == 200
    assert response.json() == {"revoked_sessions": 2}
    for token in (first, second):
        assert api.client.get("/api/v1/auth/me", headers=api.bearer(token)).status_code == 401
    # The account itself still works.
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 200
    assert api.client.get("/api/v1/auth/me", headers=api.bearer(admin_token)).status_code == 200


def test_soft_delete_user(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)

    response = api.client.delete(f"{USERS}/{created['id']}", headers=api.bearer(admin_token))

    assert response.status_code == 204
    assert api.client.get(f"{USERS}/{created['id']}", headers=api.bearer(admin_token)).status_code == 404
    assert api.login("grace@example.com", NEW_USER["password"]).status_code == 401
    assert api.sql("SELECT deleted_at IS NOT NULL FROM users WHERE id = :id", id=uuid.UUID(created["id"])) == [(True,)]
    # The email address can be used again for a new account.
    _create(api, admin_token)


def test_administrator_reset_link_flow(api: Api, admin_token: str) -> None:
    created = _create(api, admin_token)
    old_access = api.token_for("grace@example.com", NEW_USER["password"])

    response = api.client.post(f"{USERS}/{created['id']}/password-reset", headers=api.bearer(admin_token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    url = response.json()["reset_url"]
    assert url.startswith("http://localhost:5173/reset-password#token=")
    token = url.split("#token=", 1)[1]
    done = api.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "set by the user 2026"})
    assert done.status_code == 200
    assert api.client.get("/api/v1/auth/me", headers=api.bearer(old_access)).status_code == 401
    assert api.login("grace@example.com", "set by the user 2026").status_code == 200


def test_reset_links_can_be_disabled(api_factory) -> None:  # type: ignore[no-untyped-def]
    api = api_factory(password_reset_admin_links_enabled=False)
    api.create_user(ADMIN_EMAIL, superuser=True)
    admin = api.token_for(ADMIN_EMAIL)
    user_id = api.create_user("grace@example.com")

    response = api.client.post(f"{USERS}/{user_id}/password-reset", headers=api.bearer(admin))

    assert response.status_code == 409
    assert api.sql("SELECT count(*) FROM password_reset_tokens") == [(0,)]


def test_administrator_cannot_disable_or_delete_protected_accounts(api: Api, admin_token: str) -> None:
    admin_id = api.sql("SELECT id FROM users WHERE email = :e", e=ADMIN_EMAIL)[0][0]

    for action in ("deactivate",):
        response = api.client.post(f"{USERS}/{admin_id}/{action}", headers=api.bearer(admin_token))
        assert response.status_code == 409
    assert api.client.delete(f"{USERS}/{admin_id}", headers=api.bearer(admin_token)).status_code == 409
    assert api.client.get("/api/v1/auth/me", headers=api.bearer(admin_token)).status_code == 200


# --- authorization ---------------------------------------------------------------------------------


@pytest.fixture
def user_token(api: Api, admin_token: str) -> str:
    api.create_user("plain@example.com", full_name="Plain User")
    token = api.token_for("plain@example.com")
    api.clear_cookies()
    return token


def test_ordinary_user_cannot_create_users(api: Api, user_token: str) -> None:
    response = api.client.post(USERS, json=NEW_USER, headers=api.bearer(user_token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert api.sql("SELECT count(*) FROM users WHERE email = 'grace@example.com'") == [(0,)]


def test_ordinary_user_cannot_deactivate_or_manage_users(api: Api, user_token: str) -> None:
    admin_id = api.sql("SELECT id FROM users WHERE email = :e", e=ADMIN_EMAIL)[0][0]
    headers = api.bearer(user_token)

    responses = [
        api.client.post(f"{USERS}/{admin_id}/deactivate", headers=headers),
        api.client.post(f"{USERS}/{admin_id}/revoke-sessions", headers=headers),
        api.client.post(f"{USERS}/{admin_id}/password-reset", headers=headers),
        api.client.patch(f"{USERS}/{admin_id}", json={"full_name": "Pwned"}, headers=headers),
        api.client.delete(f"{USERS}/{admin_id}", headers=headers),
        api.client.get(USERS, headers=headers),
        api.client.get(f"{USERS}/{admin_id}", headers=headers),
        api.client.get("/api/v1/settings", headers=headers),
    ]

    assert [response.status_code for response in responses] == [403] * len(responses)
    assert api.sql("SELECT is_active FROM users WHERE id = :id", id=admin_id) == [(True,)]


def test_user_cannot_elevate_own_privileges(api: Api, admin_token: str, user_token: str) -> None:
    plain_id = api.sql("SELECT id FROM users WHERE email = 'plain@example.com'")[0][0]

    self_patch = api.client.patch(f"{USERS}/{plain_id}", json={"is_superuser": True}, headers=api.bearer(user_token))
    admin_patch = api.client.patch(f"{USERS}/{plain_id}", json={"is_superuser": True}, headers=api.bearer(admin_token))
    create_admin = api.client.post(USERS, json={**NEW_USER, "is_superuser": True}, headers=api.bearer(admin_token))
    login_claim = api.client.post(
        "/api/v1/auth/login", json={"email": "plain@example.com", "password": PASSWORD, "is_superuser": True}
    )

    assert self_patch.status_code == 403
    assert admin_patch.status_code == 422  # unknown fields are rejected, never ignored
    assert create_admin.status_code == 422
    assert login_claim.status_code == 422
    assert api.sql("SELECT count(*) FROM users WHERE is_superuser") == [(1,)]
    me = api.client.get("/api/v1/auth/me", headers=api.bearer(user_token)).json()
    assert me["is_superuser"] is False and me["permissions"] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", USERS),
        ("POST", USERS),
        ("GET", f"{USERS}/00000000-0000-4000-8000-000000000000"),
        ("PATCH", f"{USERS}/00000000-0000-4000-8000-000000000000"),
        ("DELETE", f"{USERS}/00000000-0000-4000-8000-000000000000"),
        ("POST", f"{USERS}/00000000-0000-4000-8000-000000000000/activate"),
        ("POST", f"{USERS}/00000000-0000-4000-8000-000000000000/deactivate"),
        ("POST", f"{USERS}/00000000-0000-4000-8000-000000000000/revoke-sessions"),
        ("POST", f"{USERS}/00000000-0000-4000-8000-000000000000/password-reset"),
        ("GET", f"{USERS}/00000000-0000-4000-8000-000000000000/sessions"),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/logout-all"),
        ("POST", "/api/v1/auth/change-password"),
        ("GET", "/api/v1/settings"),
    ],
)
def test_unauthenticated_requests_are_rejected(api: Api, method: str, path: str) -> None:
    response = api.client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"
