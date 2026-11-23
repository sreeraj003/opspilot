"""Helpers shared by database-backed tests."""

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from app.database.session import Database
from app.models import Organization, User
from app.repositories.rbac_repository import RoleRepository
from app.security.passwords import PasswordHasher

TEST_JWT_SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
DEFAULT_PASSWORD = "Correct-Horse-42!"
# Cheap Argon2 parameters keep the suite fast; production defaults are tested separately.
FAST_ARGON2: dict[str, Any] = {"argon2_time_cost": 1, "argon2_memory_cost_kib": 1024, "argon2_parallelism": 1}


@dataclass
class SeededUser:
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    password: str


@dataclass
class Factory:
    """Creates organizations and users directly in the database."""

    database: Database
    hasher: PasswordHasher

    def organization(self, name: str = "Acme Corp", *, active: bool = True) -> uuid.UUID:
        with self.database.session() as session:
            org = Organization(
                name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}", is_active=active
            )
            session.add(org)
            session.flush()
            return org.id

    def user(
        self,
        organization_id: uuid.UUID,
        *roles: str,
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        active: bool = True,
    ) -> SeededUser:
        email = email or f"user-{uuid.uuid4().hex[:8]}@acme.io"
        with self.database.session() as session:
            user = User(
                organization_id=organization_id,
                email=email,
                full_name="Test User",
                password_hash=self.hasher.hash(password),
                is_active=active,
                roles=RoleRepository(session).get_by_codes(roles),
            )
            session.add(user)
            session.flush()
            return SeededUser(user.id, organization_id, email, password)


def login(client: TestClient, user: SeededUser) -> dict[str, Any]:
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": user.password})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(tokens: dict[str, Any] | str) -> dict[str, str]:
    token = tokens if isinstance(tokens, str) else tokens["access_token"]
    return {"Authorization": f"Bearer {token}"}
