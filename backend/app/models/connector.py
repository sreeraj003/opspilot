"""External GET API connectors (Module 07).

OpsPilot never connects to a company's business database. Each company exposes
its own HTTPS endpoint that accepts SQL in a GET query parameter, runs it with
least-privilege, read-only database access and returns JSON. An
``ExternalAPIConnector`` holds how to reach that endpoint.

* ``encrypted_credentials`` and the secret values inside ``custom_headers``
  are AES-256-GCM ciphertext (``app.connectors.security.credentials``). No
  column ever holds a plaintext secret, and none is returned by the API.
* ``http_method`` is always ``GET`` (check constraint): the connector never
  switches to another method.
* Names are unique among live connectors, so are endpoint URLs, and at most one
  active connector serves a schema source, so execution is never ambiguous.
* Connectors are soft-deleted (audit records keep pointing at them); deleting
  one also erases its ciphertext.

``ConnectorAuditEvent`` is the append-only audit log of connector operations.
It never contains credentials or SQL text, only a SHA-256 fingerprint and the
length of the SQL.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

CONNECTOR_NAME_MAX_LENGTH = 150
URL_MAX_LENGTH = 2048
PATH_MAX_LENGTH = 512
PARAMETER_NAME_MAX_LENGTH = 64
HEADER_NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 4000
ERROR_SUMMARY_MAX_LENGTH = 500

AUTHENTICATION_TYPES = ("none", "api_key", "bearer_token", "custom_header")
TEST_STATUSES = ("untested", "passed", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class ExternalAPIConnector(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "external_api_connectors"
    __table_args__ = (
        CheckConstraint(f"char_length(btrim(name)) BETWEEN 1 AND {CONNECTOR_NAME_MAX_LENGTH}", name="name_length"),
        CheckConstraint(
            f"description IS NULL OR char_length(description) <= {DESCRIPTION_MAX_LENGTH}", name="description_length"
        ),
        CheckConstraint("http_method = 'GET'", name="http_method_get"),
        CheckConstraint("base_url ~ '^https?://'", name="base_url_scheme"),
        CheckConstraint("endpoint_path ~ '^/'", name="endpoint_path_absolute"),
        CheckConstraint("health_check_path IS NULL OR health_check_path ~ '^/'", name="health_check_path_absolute"),
        CheckConstraint(_in("authentication_type", AUTHENTICATION_TYPES), name="authentication_type_valid"),
        CheckConstraint(
            "(authentication_type = 'none') = (encrypted_credentials IS NULL) OR deleted_at IS NOT NULL",
            name="credentials_match_authentication",
        ),
        CheckConstraint(
            "(authentication_type IN ('api_key', 'custom_header')) = (auth_header_name IS NOT NULL)",
            name="auth_header_name_required",
        ),
        CheckConstraint("encrypted_credentials IS NULL OR left(encrypted_credentials, 3) = 'v1:'", name="credentials_encrypted"),
        CheckConstraint("jsonb_typeof(custom_headers) = 'array'", name="custom_headers_is_array"),
        CheckConstraint("jsonb_typeof(extra_query_params) = 'array'", name="extra_query_params_is_array"),
        CheckConstraint("jsonb_typeof(response_contract) = 'object'", name="response_contract_is_object"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 600", name="timeout_range"),
        CheckConstraint("connect_timeout_seconds BETWEEN 1 AND timeout_seconds", name="connect_timeout_range"),
        CheckConstraint("read_timeout_seconds BETWEEN 1 AND timeout_seconds", name="read_timeout_range"),
        CheckConstraint("max_response_size_bytes BETWEEN 1024 AND 536870912", name="max_response_size_range"),
        CheckConstraint("max_sql_length BETWEEN 1 AND 65536", name="max_sql_length_range"),
        CheckConstraint(_in("last_test_status", TEST_STATUSES), name="last_test_status_valid"),
        Index(
            "ix_external_api_connectors_name_live", func.lower(text("name")), unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_external_api_connectors_endpoint_live", "base_url", "endpoint_path", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # One active connector per schema source: the executor never has to choose.
        Index(
            "ix_external_api_connectors_active_source", "schema_source_id", unique=True,
            postgresql_where=text("is_active AND deleted_at IS NULL"),
        ),
        Index("ix_external_api_connectors_schema_source_id", "schema_source_id"),
        {"comment": "Company GET SQL API endpoints. Secrets are AES-256-GCM ciphertext only."},
    )

    name: Mapped[str] = mapped_column(String(CONNECTOR_NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    schema_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_sources.id", ondelete="RESTRICT"))
    # Lower-case scheme and host, optional path prefix, no trailing slash.
    base_url: Mapped[str] = mapped_column(String(URL_MAX_LENGTH))
    endpoint_path: Mapped[str] = mapped_column(String(PATH_MAX_LENGTH))
    http_method: Mapped[str] = mapped_column(String(8), default="GET", server_default=text("'GET'"))
    sql_parameter_name: Mapped[str] = mapped_column(
        String(PARAMETER_NAME_MAX_LENGTH), default="sql", server_default=text("'sql'")
    )
    # [["name", "value"], ...]: explicitly configured, non-sensitive query parameters.
    extra_query_params: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    authentication_type: Mapped[str] = mapped_column(String(32), default="none", server_default=text("'none'"))
    # Header that carries an API key or custom credential (not secret itself).
    auth_header_name: Mapped[str | None] = mapped_column(String(HEADER_NAME_MAX_LENGTH))
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    # [{"name", "secret": false, "value"}, {"name", "secret": true, "encrypted_value"}]
    custom_headers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    health_check_path: Mapped[str | None] = mapped_column(String(PATH_MAX_LENGTH))
    # app.connectors.adapters.response.ResponseContract
    response_contract: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{\"format\": \"envelope\"}'::jsonb")
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, server_default=text("30"))
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=5, server_default=text("5"))
    read_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, server_default=text("30"))
    max_response_size_bytes: Mapped[int] = mapped_column(Integer, default=5 * 1024 * 1024, server_default=text("5242880"))
    max_sql_length: Mapped[int] = mapped_column(Integer, default=4000, server_default=text("4000"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_tested_at: Mapped[datetime | None] = mapped_column()
    last_test_status: Mapped[str] = mapped_column(String(16), default="untested", server_default=text("'untested'"))
    last_error_category: Mapped[str | None] = mapped_column(String(64))
    last_error_summary: Mapped[str | None] = mapped_column(String(ERROR_SUMMARY_MAX_LENGTH))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    def __repr__(self) -> str:  # never include credentials
        return f"ExternalAPIConnector(name={self.name!r}, active={self.is_active})"

    @property
    def has_credentials(self) -> bool:
        return self.encrypted_credentials is not None


class ConnectorOperation:
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    TESTED = "tested"
    EXECUTED = "executed"
    CREDENTIALS_ROTATED = "credentials_rotated"


class ConnectorAuditEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only: one row per connector operation. Never credentials, never SQL text."""

    __tablename__ = "connector_audit_events"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'failure', 'rejected')", name="status_valid"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("response_size_bytes IS NULL OR response_size_bytes >= 0", name="response_size_non_negative"),
        Index("ix_connector_audit_events_connector_created", "connector_id", "created_at"),
        Index("ix_connector_audit_events_created_at", "created_at"),
        Index("ix_connector_audit_events_execution_reference", "execution_reference"),
        {"comment": "Append-only audit log of connector operations. No credentials and no SQL text."},
    )

    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("external_api_connectors.id", ondelete="SET NULL")
    )
    requesting_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    operation_type: Mapped[str] = mapped_column(String(32))
    execution_reference: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[int | None] = mapped_column(Integer)
    # SHA-256 of the SQL and its length: correlation without storing the statement or its literals.
    sql_fingerprint: Mapped[str | None] = mapped_column(String(64))
    sql_length: Mapped[int | None] = mapped_column(Integer)
    # Non-secret context: changed field names, the test step that failed. Never values of secrets.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
