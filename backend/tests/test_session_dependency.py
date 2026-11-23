from collections.abc import Callable

from fastapi.testclient import TestClient

from app.api.deps import DbSessionDep
from app.core.config import Settings
from app.main import create_app
from app.models import Organization


def test_commit_failure_is_reported_before_the_response(
    make_settings: Callable[..., Settings], db_overrides: dict, database
) -> None:
    app = create_app(make_settings(**db_overrides))

    @app.post("/probe")
    def probe(session: DbSessionDep) -> dict:
        # Not flushed: the unique-slug violation only surfaces at commit time.
        session.add(Organization(name="A", slug="duplicate-slug"))
        session.add(Organization(name="B", slug="duplicate-slug"))
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/probe").status_code == 500
