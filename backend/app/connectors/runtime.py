"""The in-memory, decrypted form of a connector used for one request.

Built by ``ConnectorService`` right before a request and discarded afterwards.
Headers (which include decrypted credentials) are excluded from ``repr`` and
never logged or returned.
"""

import uuid
from dataclasses import dataclass, field

from app.connectors.adapters.http_client import Timeouts
from app.connectors.adapters.response import ResponseContract


@dataclass(frozen=True, slots=True)
class ConnectorRuntimeConfig:
    connector_id: uuid.UUID
    schema_source_id: uuid.UUID
    current_schema_version: int
    is_active: bool
    url: str
    sql_parameter_name: str
    extra_query_params: tuple[tuple[str, str], ...]
    contract: ResponseContract
    timeouts: Timeouts
    max_response_size_bytes: int
    max_sql_length: int
    health_check_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict, repr=False)
