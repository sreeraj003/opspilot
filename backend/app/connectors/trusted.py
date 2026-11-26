"""The trusted query execution boundary.

The connector executes only ``ValidatedQueryRequest`` objects, never plain
strings, and only ones sealed by the ``ExecutionApprovalAuthority`` of this
backend process. A request carries the exact SQL, who asked for it, the schema
source and version it was validated against, and references to the validation
and permission decisions that approved it.

* **Sealed.** The authority signs every field with a random per-process HMAC
  key that never leaves memory. A request built any other way (constructed
  by hand, copied with changes, deserialised from a client or produced by an
  LLM) fails verification. Requests also expire after a short time.
* **Issuer-restricted.** Each request names the component that approved it.
  The execution adapter accepts only issuers in ``PERMITTED_ISSUERS``.
  Module 07 has exactly one: the connector test, which can produce only the
  fixed ``CONNECTOR_TEST_SQL``. The SQL Security and Execution Guard module
  will add its own issuer (and its own validation) here; until then no other
  SQL can reach the HTTP client.
* **Not self-approving.** The connector service and adapters only *verify*
  requests. The only issuing method in this module is
  ``issue_connector_test``, which ignores any caller-provided SQL.

There is no API endpoint that accepts SQL.
"""

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.connectors.errors import TrustedRequestRejectedError

CONNECTOR_TEST_SQL = "SELECT 1 AS opspilot_connection_test"
CONNECTOR_TEST_ISSUER = "connector_test"
CONNECTOR_TEST_VALIDATION_REFERENCE = "system:fixed-connector-test-query"

# Issuers whose requests the execution adapter accepts. The SQL security guard module adds its issuer.
PERMITTED_ISSUERS: frozenset[str] = frozenset({CONNECTOR_TEST_ISSUER})
# SQL each issuer is limited to, when it is limited to fixed statements.
FIXED_SQL_BY_ISSUER: dict[str, frozenset[str]] = {CONNECTOR_TEST_ISSUER: frozenset({CONNECTOR_TEST_SQL})}

DEFAULT_MAX_AGE = timedelta(seconds=120)
Reference = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:_.\-/]+$")]


class ValidatedQueryRequest(BaseModel):
    """An approved, immutable request to run one exact SQL statement through one connector."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    connector_id: uuid.UUID
    exact_sql: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    requesting_user_id: uuid.UUID
    schema_source_id: uuid.UUID
    schema_version: Annotated[int, Field(ge=1)]
    validation_reference: Reference
    permission_context_reference: Reference
    approved_at: datetime
    issuer: Reference
    execution_reference: uuid.UUID
    seal: Annotated[str, Field(min_length=64, max_length=64)]

    def __repr__(self) -> str:  # never print the SQL itself
        return (
            f"ValidatedQueryRequest(connector_id={self.connector_id}, issuer={self.issuer!r}, "
            f"execution_reference={self.execution_reference}, sql_length={len(self.exact_sql)})"
        )

    __str__ = __repr__

    @property
    def sql_fingerprint(self) -> str:
        """SHA-256 of the SQL: lets audit records be correlated without storing the statement."""
        return hashlib.sha256(self.exact_sql.encode("utf-8")).hexdigest()


def _canonical(fields: dict[str, object]) -> bytes:
    return json.dumps(
        {k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in sorted(fields.items())},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


class ExecutionApprovalAuthority:
    """Seals trusted requests and verifies seals. One instance per process (``app.state``)."""

    def __init__(self, key: bytes | None = None, *, max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        self._key = key or os.urandom(32)
        self.max_age = max_age

    def __repr__(self) -> str:
        return "ExecutionApprovalAuthority()"

    def _seal(self, fields: dict[str, object]) -> str:
        return hmac.new(self._key, _canonical(fields), hashlib.sha256).hexdigest()

    def _issue(
        self,
        *,
        issuer: str,
        connector_id: uuid.UUID,
        exact_sql: str,
        requesting_user_id: uuid.UUID,
        schema_source_id: uuid.UUID,
        schema_version: int,
        validation_reference: str,
        permission_context_reference: str,
    ) -> ValidatedQueryRequest:
        fields: dict[str, object] = {
            "connector_id": connector_id,
            "exact_sql": exact_sql,
            "requesting_user_id": requesting_user_id,
            "schema_source_id": schema_source_id,
            "schema_version": schema_version,
            "validation_reference": validation_reference,
            "permission_context_reference": permission_context_reference,
            "approved_at": datetime.now(UTC),
            "issuer": issuer,
            "execution_reference": uuid.uuid4(),
        }
        return ValidatedQueryRequest(**fields, seal=self._seal(fields))  # type: ignore[arg-type]

    def issue_connector_test(
        self, *, connector_id: uuid.UUID, requesting_user_id: uuid.UUID, schema_source_id: uuid.UUID, schema_version: int
    ) -> ValidatedQueryRequest:
        """The fixed, system-generated connection test query. Never takes SQL from a caller."""
        return self._issue(
            issuer=CONNECTOR_TEST_ISSUER,
            connector_id=connector_id,
            exact_sql=CONNECTOR_TEST_SQL,
            requesting_user_id=requesting_user_id,
            schema_source_id=schema_source_id,
            schema_version=schema_version,
            validation_reference=CONNECTOR_TEST_VALIDATION_REFERENCE,
            permission_context_reference=f"user:{requesting_user_id}:connector.test",
        )

    def verify(self, request: object, *, now: datetime | None = None) -> ValidatedQueryRequest:
        """Raise ``TrustedRequestRejectedError`` unless ``request`` is a sealed, current, permitted request."""
        if not isinstance(request, ValidatedQueryRequest):
            raise TrustedRequestRejectedError("Only validated query requests can be executed; raw SQL is refused.")
        fields = request.model_dump(exclude={"seal"})
        if not hmac.compare_digest(self._seal(fields), request.seal):
            raise TrustedRequestRejectedError("The query request was not approved by this server.")
        if request.issuer not in PERMITTED_ISSUERS:
            raise TrustedRequestRejectedError("Queries from this source cannot be executed yet.")
        fixed = FIXED_SQL_BY_ISSUER.get(request.issuer)
        if fixed is not None and request.exact_sql not in fixed:
            raise TrustedRequestRejectedError("This approval covers only its fixed system query.")
        current = now or datetime.now(UTC)
        approved = request.approved_at if request.approved_at.tzinfo else request.approved_at.replace(tzinfo=UTC)
        if approved > current + timedelta(seconds=5) or current - approved > self.max_age:
            raise TrustedRequestRejectedError("The query approval has expired.")
        return request
