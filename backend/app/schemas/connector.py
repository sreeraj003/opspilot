"""Request and response contracts of the external API connector API (Module 07).

Write schemas forbid unknown fields. Secrets arrive as ``SecretStr`` (never
shown in reprs, logs or validation errors) and are never part of any response:
responses carry only masked indicators (``has_credentials``, ``has_value``).

No contract here accepts SQL. The connector test runs a fixed, system-generated
query (``app.connectors.trusted.CONNECTOR_TEST_SQL``).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.connectors.adapters.response import ResponseContract
from app.connectors.validators.config import (
    MAX_CUSTOM_HEADERS,
    MAX_EXTRA_QUERY_PARAMS,
    normalize_base_url,
    validate_header_name,
    validate_header_value,
    validate_parameter_name,
    validate_path,
    validate_secret,
)
from app.models.connector import (
    AUTHENTICATION_TYPES,
    CONNECTOR_NAME_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    TEST_STATUSES,
)

_WRITE_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)

AuthenticationType = Literal["none", "api_key", "bearer_token", "custom_header"]
TestStatus = Literal["untested", "passed", "failed"]
assert set(AuthenticationType.__args__) == set(AUTHENTICATION_TYPES)  # type: ignore[attr-defined]
assert set(TestStatus.__args__) == set(TEST_STATUSES)  # type: ignore[attr-defined]

DEFAULT_API_KEY_HEADER = "X-API-Key"
MASK = "••••••••"


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Must not be empty.")
    return value


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _secret(value: SecretStr | None) -> SecretStr | None:
    if value is not None:
        validate_secret(value.get_secret_value())
    return value


ConnectorName = Annotated[str, Field(min_length=1, max_length=CONNECTOR_NAME_MAX_LENGTH), AfterValidator(_clean_name)]
Description = Annotated[str, Field(max_length=DESCRIPTION_MAX_LENGTH), AfterValidator(_clean_text)]
BaseUrl = Annotated[str, Field(min_length=8, max_length=2048), AfterValidator(normalize_base_url)]
EndpointPath = Annotated[str, Field(min_length=1, max_length=512), AfterValidator(validate_path)]
HealthPath = Annotated[str, Field(min_length=1, max_length=512), AfterValidator(lambda v: validate_path(v, label="The health check path"))]
ParameterName = Annotated[
    str, Field(min_length=1, max_length=64), AfterValidator(lambda v: validate_parameter_name(v, label="The SQL parameter name"))
]
HeaderName = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(validate_header_name)]
Secret = Annotated[SecretStr, AfterValidator(_secret)]


class QueryParam(BaseModel):
    """An additional, non-sensitive query parameter sent with every request (for example ``format=json``)."""

    model_config = _WRITE_CONFIG

    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(max_length=256)]


class CustomHeaderInput(BaseModel):
    """A header sent with every request. Secret values are encrypted; omit ``value`` to keep a stored secret."""

    model_config = _WRITE_CONFIG

    name: HeaderName
    secret: bool = False
    value: SecretStr | None = None

    @model_validator(mode="after")
    def _check_value(self) -> Self:
        if self.value is not None:
            validate_header_value(self.value.get_secret_value())
        elif not self.secret:
            raise ValueError(f"The header '{self.name}' needs a value.")
        return self


def _unique_headers(headers: list[CustomHeaderInput] | None) -> list[CustomHeaderInput] | None:
    if headers is None:
        return None
    seen: set[str] = set()
    for header in headers:
        key = header.name.lower()
        if key in seen:
            raise ValueError(f"The header '{header.name}' is listed more than once.")
        if key == "authorization":
            raise ValueError("Use the bearer token authentication type instead of an Authorization header.")
        seen.add(key)
    return headers


CustomHeaders = Annotated[list[CustomHeaderInput], Field(max_length=MAX_CUSTOM_HEADERS), AfterValidator(_unique_headers)]
QueryParams = Annotated[list[QueryParam], Field(max_length=MAX_EXTRA_QUERY_PARAMS)]


class _ConnectorSettings(BaseModel):
    """Fields shared by create and update; everything optional here."""

    model_config = _WRITE_CONFIG

    name: ConnectorName | None = None
    description: Description | None = None
    schema_source_id: uuid.UUID | None = None
    base_url: BaseUrl | None = None
    endpoint_path: EndpointPath | None = None
    # Accepted for explicitness only: connectors always use GET and never switch method.
    http_method: Literal["GET"] | None = None
    sql_parameter_name: ParameterName | None = None
    extra_query_params: QueryParams | None = None
    authentication_type: AuthenticationType | None = None
    auth_header_name: HeaderName | None = None
    # API key, bearer token or custom credential value. On update, omit it to keep the stored secret.
    credential: Secret | None = None
    custom_headers: CustomHeaders | None = None
    health_check_path: HealthPath | None = None
    response_contract: ResponseContract | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] | None = None
    connect_timeout_seconds: Annotated[int, Field(ge=1, le=600)] | None = None
    read_timeout_seconds: Annotated[int, Field(ge=1, le=600)] | None = None
    max_response_size_bytes: Annotated[int, Field(ge=1024, le=512 * 1024 * 1024)] | None = None
    max_sql_length: Annotated[int, Field(ge=1, le=65536)] | None = None

    @field_validator("auth_header_name")
    @classmethod
    def _auth_header(cls, value: str | None) -> str | None:
        if value is not None and value.lower() == "authorization":
            raise ValueError("Use the bearer token authentication type for the Authorization header.")
        return value


class ConnectorCreate(_ConnectorSettings):
    name: ConnectorName  # type: ignore[assignment]
    schema_source_id: uuid.UUID  # type: ignore[assignment]
    base_url: BaseUrl  # type: ignore[assignment]
    endpoint_path: EndpointPath  # type: ignore[assignment]
    http_method: Literal["GET"] = "GET"
    sql_parameter_name: ParameterName = "sql"
    extra_query_params: QueryParams = []
    authentication_type: AuthenticationType = "none"
    custom_headers: CustomHeaders = []
    response_contract: ResponseContract = ResponseContract()
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30
    connect_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 5
    read_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30
    max_response_size_bytes: Annotated[int, Field(ge=1024, le=512 * 1024 * 1024)] = 5 * 1024 * 1024
    max_sql_length: Annotated[int, Field(ge=1, le=65536)] = 4000


class ConnectorUpdate(_ConnectorSettings):
    """Only fields present are changed. ``credential`` omitted (or null) keeps the stored secret."""


class ConnectorStatusUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    is_active: bool


# --- responses ------------------------------------------------------------------------------------------------


class QueryParamRead(BaseModel):
    name: str
    value: str


class CustomHeaderRead(BaseModel):
    name: str
    secret: bool
    # Only for non-secret headers. Secret values are never returned.
    value: str | None = None
    has_value: bool


class ConnectorRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    schema_source_id: uuid.UUID
    schema_source_name: str | None = None
    schema_source_available: bool = True
    base_url: str
    endpoint_path: str
    endpoint_host: str
    http_method: Literal["GET"]
    sql_parameter_name: str
    extra_query_params: list[QueryParamRead]
    authentication_type: AuthenticationType
    auth_header_name: str | None
    has_credentials: bool
    credential_mask: str | None = Field(description="A fixed mask when a credential is stored; never any part of it.")
    custom_headers: list[CustomHeaderRead]
    health_check_path: str | None
    response_contract: ResponseContract
    timeout_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int
    max_response_size_bytes: int
    max_sql_length: int
    is_active: bool
    last_tested_at: datetime | None
    last_test_status: TestStatus
    last_error_category: str | None
    last_error_summary: str | None
    destination_allowed: bool
    destination_problem: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class ConnectorPage(BaseModel):
    items: list[ConnectorRead]
    total: int
    offset: int
    limit: int


TestStepName = Literal["configuration", "destination", "network", "tls", "authentication", "response_format"]


class ConnectorTestStep(BaseModel):
    name: TestStepName
    label: str
    status: Literal["passed", "failed", "skipped"]


class ConnectorTestResult(BaseModel):
    connector_id: uuid.UUID
    status: Literal["passed", "failed"]
    method: Literal["health_endpoint", "test_query"]
    message: str
    error_category: str | None = None
    http_status: int | None = None
    row_count: int | None = None
    duration_ms: int
    tested_at: datetime
    execution_reference: uuid.UUID
    steps: list[ConnectorTestStep]


class ConnectorHealth(BaseModel):
    connector_id: uuid.UUID
    status: Literal["ready", "inactive", "untested", "failing", "misconfigured"]
    is_active: bool
    last_test_status: TestStatus
    last_tested_at: datetime | None
    last_error_category: str | None
    last_error_summary: str | None
    destination_allowed: bool
    destination_problem: str | None = None
    credentials_available: bool
    schema_source_available: bool
    checked_at: datetime


class ConnectorEnvironment(BaseModel):
    """Server-side connector settings an administrator needs when configuring a connector. No secrets."""

    encryption_configured: bool
    allowed_hosts: list[str]
    private_networks_approved: int
    http_allowed: bool
    max_url_length: int
    max_response_size_limit: int
    max_timeout_seconds: int
    test_query: str


class ConnectorAuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connector_id: uuid.UUID | None
    operation_type: str
    status: str
    requesting_user_id: uuid.UUID | None
    requesting_user_name: str | None = None
    execution_reference: str | None
    duration_ms: int | None
    response_size_bytes: int | None
    http_status: int | None
    row_count: int | None
    error_category: str | None
    schema_version: int | None
    sql_length: int | None
    details: dict[str, Any]
    created_at: datetime


class ConnectorAuditPage(BaseModel):
    items: list[ConnectorAuditRead]
    total: int
    offset: int
    limit: int
