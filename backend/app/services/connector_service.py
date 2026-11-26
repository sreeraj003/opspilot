"""External API connectors: configuration, credentials, testing and execution (Module 07).

Route-level permission checks happen first (``require_permission``); this
service enforces what holds whatever the caller may do:

* **Configuration is validated outside the routes**: destination (static SSRF
  checks and the host allow-list), timeouts and limits within the server's
  ceilings, header and parameter rules, the associated schema source, and no
  duplicate names, duplicate endpoints or two active connectors for one schema.
* **Credentials** are encrypted with ``CredentialCipher`` before they are
  stored, are never returned, and are decrypted only into a short-lived
  ``ConnectorRuntimeConfig``. An omitted credential on update keeps the stored
  one. Without an encryption key nothing secret can be stored or used.
* **Execution** goes through ``ConnectorExecutionAdapter`` with a sealed
  ``ValidatedQueryRequest`` only. This module's only issuer is the connector
  test with its fixed query; no method here accepts SQL text.
* **Audit**: every create, update, delete, (de)activation, test and execution
  writes a ``ConnectorAuditEvent`` (never credentials, never SQL text).
* Network calls never run inside a database transaction.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.adapters.execution import ConnectorExecutionAdapter
from app.connectors.adapters.http_client import Timeouts
from app.connectors.adapters.response import NormalizedResult, ResponseContract
from app.connectors.errors import ConnectorExecutionError, ErrorCategory, TrustedRequestRejectedError
from app.connectors.runtime import ConnectorRuntimeConfig
from app.connectors.security.credentials import (
    CredentialCipher,
    CredentialDecryptionError,
    EncryptionNotConfiguredError,
    require_cipher,
)
from app.connectors.security.destinations import DestinationError, DestinationPolicy
from app.connectors.trusted import CONNECTOR_TEST_SQL, ExecutionApprovalAuthority, ValidatedQueryRequest
from app.connectors.validators.config import join_url, validate_query_param
from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, InvalidInputError, NotFoundError
from app.core.logging import is_sensitive_key
from app.database.transaction import transaction
from app.models.connector import ConnectorAuditEvent, ConnectorOperation, ExternalAPIConnector
from app.models.schema_registry import SchemaSource
from app.models.user import User
from app.repositories.base_repository import Page
from app.repositories.connector_repository import ConnectorAuditRepository, ConnectorQuery, ConnectorRepository
from app.schemas.connector import (
    DEFAULT_API_KEY_HEADER,
    ConnectorCreate,
    ConnectorUpdate,
    CustomHeaderInput,
)

logger = logging.getLogger("app.connectors")

_UNIQUE_VIOLATION = "23505"
_HEADER_PURPOSE = "header:"
# Fields whose change means the last test result no longer describes the connector.
_CONNECTION_FIELDS = frozenset(
    {
        "base_url",
        "endpoint_path",
        "sql_parameter_name",
        "extra_query_params",
        "authentication_type",
        "auth_header_name",
        "credential",
        "custom_headers",
        "health_check_path",
        "response_contract",
        "schema_source_id",
        "timeout_seconds",
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "max_response_size_bytes",
    }
)


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- errors ------------------------------------------------------------------------------------------------


class ConnectorNotFoundError(NotFoundError):
    message = "The connector was not found."


class DestinationRejectedError(InvalidInputError):
    code = "CONNECTOR_DESTINATION_REJECTED"


class ConnectorConfigurationError(InvalidInputError):
    code = "CONNECTOR_CONFIGURATION_INVALID"


class ConnectorUnavailableError(AppError):
    status_code = 503
    code = "CONNECTOR_UNAVAILABLE"
    message = "The connector service is not available."


# --- results ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectorTestOutcome:
    connector_id: uuid.UUID
    passed: bool
    method: str
    message: str
    error_category: str | None
    http_status: int | None
    row_count: int | None
    duration_ms: int
    tested_at: datetime
    execution_reference: uuid.UUID
    steps: list[dict[str, str]]


_STEPS = (
    ("configuration", "Configuration is valid"),
    ("destination", "Destination host is permitted"),
    ("network", "DNS and network connection"),
    ("tls", "TLS certificate verified"),
    ("authentication", "Authentication accepted"),
    ("response_format", "Response has the expected format"),
)
_FAILED_STEP: dict[ErrorCategory, str] = {
    ErrorCategory.CONFIGURATION_INVALID: "configuration",
    ErrorCategory.ENCRYPTION_UNAVAILABLE: "configuration",
    ErrorCategory.VALIDATION_REJECTED: "configuration",
    ErrorCategory.REQUEST_TOO_LARGE: "configuration",
    ErrorCategory.DESTINATION_REJECTED: "destination",
    ErrorCategory.DNS_RESOLUTION_FAILED: "network",
    ErrorCategory.CONNECTION_FAILED: "network",
    ErrorCategory.TIMEOUT: "network",
    ErrorCategory.TLS_FAILED: "tls",
    ErrorCategory.AUTHENTICATION_FAILED: "authentication",
}


def _steps(failed_category: ErrorCategory | None, *, uses_tls: bool) -> list[dict[str, str]]:
    failed_step = None if failed_category is None else _FAILED_STEP.get(failed_category, "response_format")
    result: list[dict[str, str]] = []
    reached_failure = False
    for name, label in _STEPS:
        if reached_failure:
            status = "skipped"
        elif name == failed_step:
            status, reached_failure = "failed", True
        elif name == "tls" and not uses_tls:
            status = "skipped"
        else:
            status = "passed"
        result.append({"name": name, "label": label, "status": status})
    return result


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    for key in details:
        if is_sensitive_key(key):
            raise ValueError(f"audit detail {key!r} looks like a credential and must not be recorded")
    return details


class ConnectorService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        cipher: CredentialCipher | None,
        policy: DestinationPolicy,
        adapter: ConnectorExecutionAdapter | None,
        authority: ExecutionApprovalAuthority,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._policy = policy
        self._adapter = adapter
        self._authority = authority
        self._connectors = ConnectorRepository(session)
        self._audit = ConnectorAuditRepository(session)

    # =============================================================================================
    # reads
    # =============================================================================================

    def environment(self) -> dict[str, Any]:
        settings = self._settings
        return {
            "encryption_configured": self._cipher is not None,
            "allowed_hosts": list(settings.connector_allowed_hosts),
            "private_networks_approved": len(settings.connector_allowed_private_networks),
            "http_allowed": settings.connector_http_allowed,
            "max_url_length": settings.connector_max_url_length,
            "max_response_size_limit": settings.connector_max_response_bytes_limit,
            "max_timeout_seconds": settings.connector_max_timeout_seconds,
            "test_query": CONNECTOR_TEST_SQL,
        }

    async def list_connectors(
        self, query: ConnectorQuery, *, offset: int, limit: int
    ) -> tuple[Page[ExternalAPIConnector], dict[uuid.UUID, SchemaSource]]:
        page = await self._connectors.search(query, offset=offset, limit=limit)
        return page, await self._connectors.schema_sources(c.schema_source_id for c in page.items)

    async def get(self, connector_id: uuid.UUID) -> tuple[ExternalAPIConnector, SchemaSource | None]:
        connector = await self._connector(connector_id)
        sources = await self._connectors.schema_sources([connector.schema_source_id])
        return connector, sources.get(connector.schema_source_id)

    def destination_problem(self, connector: ExternalAPIConnector) -> str | None:
        """Why the configured destination is refused now (static checks only), or None."""
        try:
            self._policy.check_url(join_url(connector.base_url, connector.endpoint_path))
            if connector.health_check_path:
                self._policy.check_url(join_url(connector.base_url, connector.health_check_path))
        except DestinationError as exc:
            return exc.message
        return None

    async def health(self, connector_id: uuid.UUID) -> dict[str, Any]:
        connector, source = await self.get(connector_id)
        problem = self.destination_problem(connector)
        credentials_available = True
        try:
            self._decrypt_headers(connector)
        except AppError:
            credentials_available = False
        source_available = _source_available(source)
        if problem or not credentials_available or not source_available:
            status = "misconfigured"
        elif not connector.is_active:
            status = "inactive"
        elif connector.last_test_status == "untested":
            status = "untested"
        elif connector.last_test_status == "failed":
            status = "failing"
        else:
            status = "ready"
        return {
            "connector_id": connector.id,
            "status": status,
            "is_active": connector.is_active,
            "last_test_status": connector.last_test_status,
            "last_tested_at": connector.last_tested_at,
            "last_error_category": connector.last_error_category,
            "last_error_summary": connector.last_error_summary,
            "destination_allowed": problem is None,
            "destination_problem": problem,
            "credentials_available": credentials_available,
            "schema_source_available": source_available,
            "checked_at": utcnow(),
        }

    async def audit_history(
        self, connector_id: uuid.UUID, *, offset: int, limit: int, operation_type: str | None = None
    ) -> tuple[Page[ConnectorAuditEvent], dict[uuid.UUID, str]]:
        await self._connector(connector_id)
        return await self._audit.for_connector(connector_id, offset=offset, limit=limit, operation_type=operation_type)

    # =============================================================================================
    # writes
    # =============================================================================================

    async def create(self, data: ConnectorCreate, actor: User) -> ExternalAPIConnector:
        connector_id = uuid.uuid4()
        auth_header = self._auth_header_name(data.authentication_type, data.auth_header_name, None)
        values: dict[str, Any] = {
            "name": data.name,
            "description": data.description,
            "schema_source_id": data.schema_source_id,
            "base_url": data.base_url,
            "endpoint_path": data.endpoint_path,
            "http_method": "GET",
            "sql_parameter_name": data.sql_parameter_name,
            "extra_query_params": self._query_params(data.extra_query_params, data.sql_parameter_name),
            "authentication_type": data.authentication_type,
            "auth_header_name": auth_header,
            "health_check_path": data.health_check_path,
            "response_contract": data.response_contract.model_dump(),
            "timeout_seconds": data.timeout_seconds,
            "connect_timeout_seconds": data.connect_timeout_seconds,
            "read_timeout_seconds": data.read_timeout_seconds,
            "max_response_size_bytes": data.max_response_size_bytes,
            "max_sql_length": data.max_sql_length,
        }
        self._check_limits(values)
        self._check_destination(values["base_url"], values["endpoint_path"], values["health_check_path"])
        if data.authentication_type == "none":
            if data.credential is not None:
                raise ConnectorConfigurationError("Remove the credential, or choose an authentication type that uses one.")
            encrypted = None
        else:
            if data.credential is None:
                raise ConnectorConfigurationError("Enter the credential for the selected authentication type.")
            encrypted = self._encrypt_credential(connector_id, data.authentication_type, data.credential.get_secret_value())
        values["custom_headers"] = self._headers(connector_id, data.custom_headers, [], auth_header, data.authentication_type)
        try:
            async with transaction(self._session):
                await self._check_source(data.schema_source_id)
                await self._check_unique(values["name"], values["base_url"], values["endpoint_path"], exclude_id=None)
                connector = await self._connectors.create(
                    id=connector_id,
                    encrypted_credentials=encrypted,
                    is_active=False,
                    last_test_status="untested",
                    created_by=actor.id,
                    updated_by=actor.id,
                    **values,
                )
                await self._record(connector.id, actor.id, ConnectorOperation.CREATED, "success", details={"name": connector.name})
        except IntegrityError as exc:
            raise _conflict(exc) from None
        return connector

    async def update(self, connector_id: uuid.UUID, data: ConnectorUpdate, actor: User) -> ExternalAPIConnector:
        requested = data.model_dump(exclude_unset=True)
        for required in (
            "name", "schema_source_id", "base_url", "endpoint_path", "sql_parameter_name", "authentication_type",
            "response_contract", "timeout_seconds", "connect_timeout_seconds", "read_timeout_seconds",
            "max_response_size_bytes", "max_sql_length", "extra_query_params", "custom_headers", "http_method",
        ):
            if required in requested and requested[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        try:
            async with transaction(self._session):
                connector = await self._connector(connector_id, for_update=True)
                changes: dict[str, Any] = {}
                for name in (
                    "name", "description", "schema_source_id", "base_url", "endpoint_path", "sql_parameter_name",
                    "health_check_path", "timeout_seconds", "connect_timeout_seconds", "read_timeout_seconds",
                    "max_response_size_bytes", "max_sql_length",
                ):
                    if name in requested and getattr(connector, name) != requested[name]:
                        changes[name] = requested[name]
                if "response_contract" in requested:
                    contract = data.response_contract.model_dump()  # type: ignore[union-attr]
                    if contract != connector.response_contract:
                        changes["response_contract"] = contract
                merged = {name: changes.get(name, getattr(connector, name)) for name in (
                    "base_url", "endpoint_path", "health_check_path", "sql_parameter_name", "timeout_seconds",
                    "connect_timeout_seconds", "read_timeout_seconds", "max_response_size_bytes", "max_sql_length",
                )}
                if "extra_query_params" in requested or "sql_parameter_name" in changes:
                    params_input = data.extra_query_params if "extra_query_params" in requested else None
                    params = (
                        self._query_params(params_input, merged["sql_parameter_name"])
                        if params_input is not None
                        else self._query_params_from_stored(connector.extra_query_params, merged["sql_parameter_name"])
                    )
                    if params != connector.extra_query_params:
                        changes["extra_query_params"] = params
                self._check_limits(merged)
                self._check_destination(merged["base_url"], merged["endpoint_path"], merged["health_check_path"])

                # Authentication: an omitted credential keeps the stored one, if it still applies.
                auth_type = requested.get("authentication_type") or connector.authentication_type
                auth_header = self._auth_header_name(
                    auth_type,
                    requested.get("auth_header_name") if "auth_header_name" in requested else None,
                    connector.auth_header_name if auth_type == connector.authentication_type else None,
                )
                if auth_type != connector.authentication_type:
                    changes["authentication_type"] = auth_type
                if auth_header != connector.auth_header_name:
                    changes["auth_header_name"] = auth_header
                if auth_type == "none":
                    if data.credential is not None:
                        raise ConnectorConfigurationError("Remove the credential, or choose an authentication type that uses one.")
                    if connector.encrypted_credentials is not None:
                        changes["encrypted_credentials"] = None
                elif data.credential is not None:
                    changes["encrypted_credentials"] = self._encrypt_credential(
                        connector.id, auth_type, data.credential.get_secret_value()
                    )
                    changes["credential"] = True
                elif auth_type != connector.authentication_type or connector.encrypted_credentials is None:
                    raise ConnectorConfigurationError("Enter the credential for the selected authentication type.")

                if "custom_headers" in requested or "auth_header_name" in changes or "authentication_type" in changes:
                    headers = self._headers(
                        connector.id,
                        data.custom_headers if data.custom_headers is not None else None,
                        connector.custom_headers,
                        auth_header,
                        auth_type,
                    )
                    if headers != connector.custom_headers:
                        changes["custom_headers"] = headers

                if not changes:
                    return connector
                if "schema_source_id" in changes:
                    await self._check_source(changes["schema_source_id"])
                    if connector.is_active and await self._connectors.active_for_source(
                        changes["schema_source_id"], exclude_id=connector.id
                    ):
                        raise ConflictError("Another active connector already serves that schema.")
                await self._check_unique(
                    changes.get("name", connector.name), merged["base_url"], merged["endpoint_path"], exclude_id=connector.id
                )
                changed_fields = sorted(k for k in changes if k != "encrypted_credentials")
                if "encrypted_credentials" in changes and "credential" not in changes:
                    changed_fields.append("credential")
                for name, value in changes.items():
                    if name != "credential":
                        setattr(connector, name, value)
                if _CONNECTION_FIELDS & set(changed_fields):
                    connector.last_test_status = "untested"
                    connector.last_error_category = None
                    connector.last_error_summary = None
                connector.updated_by = actor.id
                await self._session.flush()
                await self._record(
                    connector.id, actor.id, ConnectorOperation.UPDATED, "success", details={"changed_fields": sorted(set(changed_fields))}
                )
        except IntegrityError as exc:
            raise _conflict(exc) from None
        return connector

    async def delete(self, connector_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            connector = await self._connector(connector_id, for_update=True)
            # Crypto-shredding: the ciphertext goes with the connector.
            connector.encrypted_credentials = None
            connector.custom_headers = [h for h in connector.custom_headers if not h.get("secret")]
            connector.is_active = False
            connector.updated_by = actor.id
            connector.mark_deleted()
            await self._session.flush()
            await self._record(connector.id, actor.id, ConnectorOperation.DELETED, "success", details={"name": connector.name})

    async def set_status(self, connector_id: uuid.UUID, active: bool, actor: User) -> ExternalAPIConnector:
        try:
            async with transaction(self._session):
                connector = await self._connector(connector_id, for_update=True)
                if connector.is_active == active:
                    return connector
                if active:
                    problem = self.destination_problem(connector)
                    if problem:
                        raise DestinationRejectedError(problem)
                    source = (await self._connectors.schema_sources([connector.schema_source_id])).get(connector.schema_source_id)
                    if not _source_available(source) or not source.is_active:  # type: ignore[union-attr]
                        raise ConnectorConfigurationError("The connector's schema is deleted or inactive; it cannot be activated.")
                    self._decrypt_headers(connector)  # credentials must be usable
                    if await self._connectors.active_for_source(connector.schema_source_id, exclude_id=connector.id):
                        raise ConflictError("Another active connector already serves this schema. Deactivate it first.")
                connector.is_active = active
                connector.updated_by = actor.id
                await self._session.flush()
                await self._record(
                    connector.id, actor.id, ConnectorOperation.ACTIVATED if active else ConnectorOperation.DEACTIVATED, "success"
                )
        except IntegrityError as exc:
            raise _conflict(exc) from None
        return connector

    # =============================================================================================
    # testing and execution
    # =============================================================================================

    async def test(self, connector_id: uuid.UUID, actor: User) -> ConnectorTestOutcome:
        """The safe connection test: the health endpoint if configured, else the fixed test query."""
        connector, source = await self.get(connector_id)
        uses_tls = connector.base_url.startswith("https://")
        method = "health_endpoint" if connector.health_check_path else "test_query"
        schema_version = source.schema_version if source is not None else None
        reference = uuid.uuid4()
        started = time.monotonic()
        result: NormalizedResult | None = None
        size: int | None = None
        http_status: int | None = None
        sql_fields: dict[str, Any] = {}
        error: ConnectorExecutionError | None = None
        try:
            config = self._runtime_config(connector, source)
            # End the read transaction before any network call.
            await self._session.commit()
            adapter = self._require_adapter()
            if method == "health_endpoint":
                raw = await adapter.check_health(config)
                size, http_status = raw.size, raw.status_code
            else:
                request = self._authority.issue_connector_test(
                    connector_id=connector.id,
                    requesting_user_id=actor.id,
                    schema_source_id=connector.schema_source_id,
                    schema_version=config.current_schema_version,
                )
                reference = request.execution_reference
                sql_fields = {"sql_fingerprint": request.sql_fingerprint, "sql_length": len(request.exact_sql)}
                result = await adapter.execute(request, config)
                size, http_status = result.response_size_bytes, result.http_status
        except ConnectorExecutionError as exc:
            error = exc
            http_status = exc.http_status
        duration_ms = int((time.monotonic() - started) * 1000)
        tested_at = utcnow()
        if error is None:
            message = (
                "Connection test passed: the health endpoint answered."
                if result is None
                else f"Connection test passed: the test query returned {result.row_count} row{'s' if result.row_count != 1 else ''}."
            )
        else:
            message = error.message
        category = error.category if error else None
        async with transaction(self._session):
            stored = await self._connectors.get_for_update(connector.id)
            if stored is not None:
                stored.last_tested_at = tested_at
                stored.last_test_status = "passed" if error is None else "failed"
                stored.last_error_category = category.value if category else None
                stored.last_error_summary = message[:500] if error else None
            await self._record(
                connector.id,
                actor.id,
                ConnectorOperation.TESTED,
                "success" if error is None else ("rejected" if isinstance(error, TrustedRequestRejectedError) else "failure"),
                execution_reference=str(reference),
                duration_ms=duration_ms,
                response_size_bytes=size,
                http_status=http_status,
                row_count=result.row_count if result else None,
                error_category=category.value if category else None,
                schema_version=schema_version,
                details={"method": method},
                **sql_fields,
            )
        return ConnectorTestOutcome(
            connector_id=connector.id,
            passed=error is None,
            method=method,
            message=message,
            error_category=category.value if category else None,
            http_status=http_status,
            row_count=result.row_count if result else None,
            duration_ms=duration_ms,
            tested_at=tested_at,
            execution_reference=reference,
            steps=_steps(category, uses_tls=uses_tls),
        )

    async def execute(self, request: ValidatedQueryRequest) -> NormalizedResult:
        """Run one trusted, sealed request. The internal boundary later modules call; no route exposes it."""
        if not isinstance(request, ValidatedQueryRequest):
            raise TrustedRequestRejectedError("Only validated query requests can be executed; raw SQL is refused.")
        started = time.monotonic()
        result: NormalizedResult | None = None
        error: ConnectorExecutionError | None = None
        schema_version: int | None = request.schema_version
        known_connector = False
        try:
            connector, source = await self.get(request.connector_id)
            known_connector = True
            config = self._runtime_config(connector, source)
            await self._session.commit()
            result = await self._require_adapter().execute(request, config)
        except ConnectorExecutionError as exc:
            error = exc
        except ConnectorNotFoundError:
            error = TrustedRequestRejectedError("The connector does not exist.")
        duration_ms = int((time.monotonic() - started) * 1000)
        async with transaction(self._session):
            await self._record(
                request.connector_id if known_connector else None,
                request.requesting_user_id,
                ConnectorOperation.EXECUTED,
                "success" if error is None else ("rejected" if isinstance(error, TrustedRequestRejectedError) else "failure"),
                execution_reference=str(request.execution_reference),
                duration_ms=duration_ms,
                response_size_bytes=result.response_size_bytes if result else None,
                http_status=result.http_status if result else (error.http_status if error else None),
                row_count=result.row_count if result else None,
                error_category=error.category.value if error else None,
                schema_version=schema_version,
                sql_fingerprint=request.sql_fingerprint,
                sql_length=len(request.exact_sql),
                details={"issuer": request.issuer},
            )
        if error is not None:
            raise error
        assert result is not None
        return result

    # =============================================================================================
    # key rotation
    # =============================================================================================

    async def rotate_encryption_keys(self) -> int:
        """Re-encrypt every stored secret with the primary key. Returns how many connectors changed."""
        cipher = require_cipher(self._cipher)
        changed = 0
        async with transaction(self._session):
            for connector in await self._connectors.all_with_secrets():
                touched = False
                if connector.encrypted_credentials and cipher.needs_rotation(connector.encrypted_credentials):
                    purpose = f"credentials:{connector.authentication_type}"
                    plaintext = cipher.decrypt(connector.encrypted_credentials, connector_id=connector.id, purpose=purpose)
                    connector.encrypted_credentials = cipher.encrypt(plaintext, connector_id=connector.id, purpose=purpose)
                    touched = True
                headers: list[dict[str, Any]] = []
                for header in connector.custom_headers:
                    token = header.get("encrypted_value")
                    if header.get("secret") and token and cipher.needs_rotation(token):
                        purpose = _HEADER_PURPOSE + header["name"].lower()
                        plaintext = cipher.decrypt(token, connector_id=connector.id, purpose=purpose)
                        header = {**header, "encrypted_value": cipher.encrypt(plaintext, connector_id=connector.id, purpose=purpose)}
                        touched = True
                    headers.append(header)
                if touched:
                    connector.custom_headers = headers
                    changed += 1
                    await self._record(connector.id, None, ConnectorOperation.CREDENTIALS_ROTATED, "success")
        return changed

    # =============================================================================================
    # helpers
    # =============================================================================================

    async def _connector(self, connector_id: uuid.UUID, *, for_update: bool = False) -> ExternalAPIConnector:
        connector = (
            await self._connectors.get_for_update(connector_id) if for_update else await self._connectors.get(connector_id)
        )
        if connector is None:
            raise ConnectorNotFoundError()
        return connector

    def _require_adapter(self) -> ConnectorExecutionAdapter:
        if self._adapter is None:
            raise ConnectorUnavailableError()
        return self._adapter

    async def _check_source(self, schema_source_id: uuid.UUID) -> None:
        source = (await self._connectors.schema_sources([schema_source_id])).get(schema_source_id)
        if not _source_available(source):
            raise ConnectorConfigurationError("The selected schema was not found.")

    async def _check_unique(self, name: str, base_url: str, endpoint_path: str, *, exclude_id: uuid.UUID | None) -> None:
        if await self._connectors.name_taken(name, exclude_id=exclude_id):
            raise ConflictError("A connector with this name already exists.")
        target = join_url(base_url, endpoint_path)
        for _, other_base, other_path in await self._connectors.endpoints(exclude_id=exclude_id):
            if join_url(other_base, other_path) == target:
                raise ConflictError("Another connector already uses this endpoint URL.")

    def _check_limits(self, values: dict[str, Any]) -> None:
        settings = self._settings
        total = values["timeout_seconds"]
        if total > settings.connector_max_timeout_seconds:
            raise ConnectorConfigurationError(
                f"The timeout may be at most {settings.connector_max_timeout_seconds} seconds on this server."
            )
        if values["connect_timeout_seconds"] > total or values["read_timeout_seconds"] > total:
            raise ConnectorConfigurationError("The connect and read timeouts cannot be longer than the overall timeout.")
        if values["max_response_size_bytes"] > settings.connector_max_response_bytes_limit:
            raise ConnectorConfigurationError(
                f"The maximum response size may be at most {settings.connector_max_response_bytes_limit} bytes on this server."
            )

    def _check_destination(self, base_url: str, endpoint_path: str, health_path: str | None) -> None:
        try:
            self._policy.check_url(join_url(base_url, endpoint_path))
            if health_path:
                self._policy.check_url(join_url(base_url, health_path))
        except DestinationError as exc:
            raise DestinationRejectedError(exc.message) from None

    @staticmethod
    def _auth_header_name(auth_type: str, requested: str | None, existing: str | None) -> str | None:
        if auth_type == "api_key":
            return requested or existing or DEFAULT_API_KEY_HEADER
        if auth_type == "custom_header":
            name = requested or existing
            if not name:
                raise ConnectorConfigurationError("Enter the name of the header that carries the credential.")
            return name
        if requested:
            raise ConnectorConfigurationError("A credential header name applies only to API key and custom header authentication.")
        return None

    @staticmethod
    def _query_params(params: Sequence[Any] | None, sql_parameter_name: str) -> list[list[str]]:
        result: list[list[str]] = []
        seen: set[str] = set()
        for param in params or []:
            try:
                name, value = validate_query_param(param.name, param.value, sql_parameter_name=sql_parameter_name)
            except ValueError as exc:
                raise ConnectorConfigurationError(str(exc)) from None
            if name.lower() in seen:
                raise ConnectorConfigurationError(f"The query parameter '{name}' is listed more than once.")
            seen.add(name.lower())
            result.append([name, value])
        return result

    def _query_params_from_stored(self, stored: list[Any], sql_parameter_name: str) -> list[list[str]]:
        @dataclass
        class _P:
            name: str
            value: str

        return self._query_params([_P(p[0], p[1]) for p in stored], sql_parameter_name)

    def _encrypt_credential(self, connector_id: uuid.UUID, auth_type: str, secret: str) -> str:
        cipher = require_cipher(self._cipher)
        return cipher.encrypt_json({"value": secret}, connector_id=connector_id, purpose=f"credentials:{auth_type}")

    def _headers(
        self,
        connector_id: uuid.UUID,
        requested: list[CustomHeaderInput] | None,
        existing: list[dict[str, Any]],
        auth_header: str | None,
        auth_type: str,
    ) -> list[dict[str, Any]]:
        """Stored form of the custom headers. A secret header without a value keeps its stored secret."""
        if requested is None:
            requested_headers: list[CustomHeaderInput] | None = None
        else:
            requested_headers = requested
        stored_by_name = {h["name"].lower(): h for h in existing}
        result: list[dict[str, Any]] = []
        if requested_headers is None:
            result = list(existing)
        else:
            for header in requested_headers:
                key = header.name.lower()
                if header.secret:
                    if header.value is not None:
                        token = require_cipher(self._cipher).encrypt(
                            header.value.get_secret_value(), connector_id=connector_id, purpose=_HEADER_PURPOSE + key
                        )
                        result.append({"name": header.name, "secret": True, "encrypted_value": token})
                    else:
                        previous = stored_by_name.get(key)
                        if not previous or not previous.get("secret") or not previous.get("encrypted_value"):
                            raise ConnectorConfigurationError(f"Enter a value for the secret header '{header.name}'.")
                        result.append({"name": header.name, "secret": True, "encrypted_value": previous["encrypted_value"]})
                else:
                    result.append({"name": header.name, "secret": False, "value": header.value.get_secret_value()})  # type: ignore[union-attr]
        names = {h["name"].lower() for h in result}
        if auth_header and auth_header.lower() in names:
            raise ConnectorConfigurationError(f"The header '{auth_header}' is already used for authentication.")
        if auth_type == "bearer_token" and "authorization" in names:
            raise ConnectorConfigurationError("The Authorization header is set by bearer token authentication.")
        return result

    def _decrypt_headers(self, connector: ExternalAPIConnector) -> dict[str, str]:
        """Every header the connector sends, with secrets decrypted. Never log or return the result."""
        needs_key = connector.encrypted_credentials is not None or any(h.get("secret") for h in connector.custom_headers)
        cipher = self._cipher
        if needs_key and cipher is None:
            raise EncryptionNotConfiguredError()
        headers: dict[str, str] = {}
        for header in connector.custom_headers:
            if header.get("secret"):
                headers[header["name"]] = cipher.decrypt(  # type: ignore[union-attr]
                    header["encrypted_value"], connector_id=connector.id, purpose=_HEADER_PURPOSE + header["name"].lower()
                )
            else:
                headers[header["name"]] = header["value"]
        if connector.encrypted_credentials is not None:
            secret = cipher.decrypt_json(  # type: ignore[union-attr]
                connector.encrypted_credentials, connector_id=connector.id, purpose=f"credentials:{connector.authentication_type}"
            )["value"]
            if connector.authentication_type == "bearer_token":
                headers["Authorization"] = f"Bearer {secret}"
            elif connector.auth_header_name:
                headers[connector.auth_header_name] = secret
        return headers

    def _runtime_config(self, connector: ExternalAPIConnector, source: SchemaSource | None) -> ConnectorRuntimeConfig:
        if not _source_available(source):
            raise ConnectorExecutionError(
                ErrorCategory.CONFIGURATION_INVALID, "The connector's schema was deleted. Associate the connector with a schema."
            )
        try:
            headers = self._decrypt_headers(connector)
        except (EncryptionNotConfiguredError, CredentialDecryptionError) as exc:
            raise ConnectorExecutionError(ErrorCategory.ENCRYPTION_UNAVAILABLE, exc.message) from None
        try:
            contract = ResponseContract.model_validate(connector.response_contract)
        except ValueError:
            raise ConnectorExecutionError(ErrorCategory.CONFIGURATION_INVALID, "The connector's response format is not valid.") from None
        return ConnectorRuntimeConfig(
            connector_id=connector.id,
            schema_source_id=connector.schema_source_id,
            current_schema_version=source.schema_version,  # type: ignore[union-attr]
            is_active=connector.is_active,
            url=join_url(connector.base_url, connector.endpoint_path),
            sql_parameter_name=connector.sql_parameter_name,
            extra_query_params=tuple((p[0], p[1]) for p in connector.extra_query_params),
            contract=contract,
            timeouts=Timeouts(
                connect=float(connector.connect_timeout_seconds),
                read=float(connector.read_timeout_seconds),
                total=float(connector.timeout_seconds),
            ),
            max_response_size_bytes=connector.max_response_size_bytes,
            max_sql_length=connector.max_sql_length,
            health_check_url=join_url(connector.base_url, connector.health_check_path) if connector.health_check_path else None,
            headers=headers,
        )

    async def _record(
        self,
        connector_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        operation: str,
        status: str,
        *,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        event = ConnectorAuditEvent(
            connector_id=connector_id,
            requesting_user_id=user_id,
            operation_type=operation,
            status=status,
            details=_safe_details(details or {}),
            **fields,
        )
        self._session.add(event)
        await self._session.flush()
        logger.info(
            "Connector operation",
            extra={
                "connector_id": str(connector_id) if connector_id else None,
                "operation_type": operation,
                "status": status,
                "error_category": fields.get("error_category"),
                "duration_ms": fields.get("duration_ms"),
                "execution_reference": fields.get("execution_reference"),
            },
        )


def _source_available(source: SchemaSource | None) -> bool:
    return source is not None and source.deleted_at is None


def _conflict(exc: IntegrityError) -> AppError:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        constraint = str(getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", "") or "")
        if "active_source" in constraint:
            return ConflictError("Another active connector already serves this schema.")
        if "endpoint" in constraint:
            return ConflictError("Another connector already uses this endpoint URL.")
        return ConflictError("A connector with this name already exists.")
    return ConnectorConfigurationError("The connector configuration is not valid.")
