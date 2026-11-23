import pytest
from fastapi.testclient import TestClient

from app.cli import create_platform_admin
from app.core.config import Settings
from app.database.session import Database
from tests.helpers import DEFAULT_PASSWORD, Factory, bearer


def test_create_platform_admin_bootstraps_a_super_admin(
    api: TestClient, database: Database, db_settings: Settings, factory: Factory
) -> None:
    user = create_platform_admin(
        database, email="Root@OpsPilot.io", full_name="Root", password=DEFAULT_PASSWORD, settings=db_settings
    )
    assert user.email == "root@opspilot.io"

    tokens = api.post("/api/v1/auth/login", json={"email": "root@opspilot.io", "password": DEFAULT_PASSWORD}).json()
    me = api.get("/api/v1/auth/me", headers=bearer(tokens)).json()
    assert me["roles"] == ["super_admin"]
    assert me["organization"]["slug"] == "platform"
    assert "platform.admin" in me["permissions"]

    other_org = factory.organization("Customer Co")
    assert (
        api.get("/api/v1/users", params={"organization_id": str(other_org)}, headers=bearer(tokens)).status_code == 200
    )


def test_create_platform_admin_enforces_password_policy(database: Database, db_settings: Settings) -> None:
    with pytest.raises(ValueError, match="password policy"):
        create_platform_admin(
            database, email="root@opspilot.io", full_name="Root", password="weak", settings=db_settings
        )
