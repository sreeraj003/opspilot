"""ApplicationSettingsService rules and schema validation."""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.repositories.application_setting_repository import ApplicationSettingRepository
from app.schemas.application_setting import ApplicationSettingRead, ApplicationSettingWrite
from app.services.application_settings_service import ApplicationSettingsService

pytestmark = pytest.mark.postgres


def _write(value: object, description: str | None = None) -> ApplicationSettingWrite:
    return ApplicationSettingWrite.model_validate({"value": value, "description": description})


async def test_create_get_update_delete(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        service = ApplicationSettingsService(session)
        created, was_created = await service.put_setting("ui.page_size", _write(25, "Rows per page"))
        assert was_created
        assert created.value == 25

    # Committed: visible from a new session.
    async with session_factory() as session:
        service = ApplicationSettingsService(session)
        fetched = await service.get_setting("ui.page_size")
        assert (fetched.value, fetched.description) == (25, "Rows per page")

        updated, was_created = await service.put_setting("ui.page_size", _write(50))
        assert not was_created
        assert updated.id == fetched.id
        assert (updated.value, updated.description) == (50, None)

        await service.delete_setting("ui.page_size")

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await ApplicationSettingsService(session).get_setting("ui.page_size")


async def test_missing_setting_raises_not_found(db_session: AsyncSession) -> None:
    service = ApplicationSettingsService(db_session)

    with pytest.raises(NotFoundError):
        await service.get_setting("missing")
    with pytest.raises(NotFoundError):
        await service.delete_setting("missing")


async def test_concurrent_duplicate_key_is_reported_as_conflict(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        await ApplicationSettingsService(session).put_setting("race.key", _write(1))

    async with session_factory() as session:
        service = ApplicationSettingsService(session)
        # Simulate another request inserting the key between lookup and insert.
        monkeypatch.setattr(ApplicationSettingRepository, "get_by_key", _always_missing)
        with pytest.raises(ConflictError):
            await service.put_setting("race.key", _write(2))
        assert not session.in_transaction()
    monkeypatch.undo()

    async with session_factory() as session:
        setting = await ApplicationSettingsService(session).get_setting("race.key")
        assert setting.value == 1


async def _always_missing(self: ApplicationSettingRepository, key: str) -> None:
    return None


@pytest.mark.parametrize("key", ["db_password", "smtp.password", "openai-api-key", "auth.token", "client_secret"])
async def test_secret_like_keys_are_rejected(db_session: AsyncSession, key: str) -> None:
    with pytest.raises(InvalidInputError):
        await ApplicationSettingsService(db_session).put_setting(key, _write("x"))
    assert await ApplicationSettingRepository(db_session).count() == 0


@pytest.mark.parametrize("key", ["", "Upper", "9lives", "has space", "x" * 129])
async def test_malformed_keys_are_rejected(db_session: AsyncSession, key: str) -> None:
    with pytest.raises(InvalidInputError):
        await ApplicationSettingsService(db_session).put_setting(key, _write(1))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"value": None},
        {"value": 1, "unexpected": True},
        {"value": 1, "description": "x" * 1001},
        {"value": "x" * 20000},
        {"value": {"smtp": {"password": "hunter2"}}},
        {"value": [{"api_key": "k"}]},
    ],
)
def test_invalid_write_payloads_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as caught:
        ApplicationSettingWrite.model_validate(payload)

    assert "hunter2" not in str(caught.value)


def test_valid_values_of_every_json_type_are_accepted() -> None:
    for value in ["text", 1, 1.5, True, False, [1, "a"], {"nested": {"list": [1, 2]}}, ""]:
        assert ApplicationSettingWrite.model_validate({"value": value}).value == value


async def test_read_schema_redacts_secret_like_data_written_outside_the_api(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)
    leaked_key = await repository.create(key="legacy.password", value="hunter2")
    leaked_member = await repository.create(key="mail", value={"host": "smtp.example", "password": "hunter2"})

    assert ApplicationSettingRead.model_validate(leaked_key).value == "[REDACTED]"
    assert ApplicationSettingRead.model_validate(leaked_member).value == {
        "host": "smtp.example",
        "password": "[REDACTED]",
    }
