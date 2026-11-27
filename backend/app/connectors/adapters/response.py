"""Validation and normalisation of the company backend's JSON responses.

Company backends differ, so the expected response contract is part of each
connector's configuration (``ResponseContract``). Two formats are supported,
and the adapter never guesses field names:

``envelope`` (the default, matching the reference contract)::

    {"success": true, "data": [{"total_orders": 1250}], "metadata": {"execution_time_ms": 45}}

    The success, data, metadata and error field names are configurable.
    ``success`` must be a JSON boolean. ``success: false`` is always an
    error, whatever else the body contains.

``raw_array``: the body is the JSON array of row objects itself.

Either way the rows must be a JSON array of JSON objects. Column names and
values are kept exactly as received: rows are not padded with missing keys,
values are not converted, and an empty array is a successful, empty result
(distinct from every error).
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.connectors.errors import ConnectorExecutionError, ErrorCategory
from app.connectors.validators.config import validate_field_name

ResponseFormat = Literal["envelope", "raw_array"]
_ERROR_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")


class ResponseContract(BaseModel):
    """How a connector's backend shapes its JSON. Stored with the connector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: ResponseFormat = "envelope"
    success_field: str = "success"
    data_field: str = "data"
    metadata_field: str = "metadata"
    error_field: str = "error"

    @field_validator("success_field", "data_field", "metadata_field", "error_field")
    @classmethod
    def _field_name(cls, value: str) -> str:
        return validate_field_name(value, label="Response field names")


class EnvelopeMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    execution_time_ms: float | None = Field(default=None, ge=0)


class ReferenceEnvelope(BaseModel):
    """The reference contract, for documentation and the OpenAPI schema."""

    model_config = ConfigDict(extra="allow")

    success: StrictBool
    data: list[dict[str, Any]] | None = None
    metadata: EnvelopeMetadata | None = None
    error: Any = None


@dataclass(frozen=True, slots=True)
class NormalizedResult:
    """The one internal shape every successful query result takes."""

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float | None
    response_size_bytes: int
    http_status: int
    # The envelope's metadata object as received (envelope format only), e.g. column type information.
    metadata: dict[str, Any] | None = field(default=None)

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0


def _reject_constant(value: str) -> Any:
    raise ValueError("non-standard JSON constant")


def parse_json(body: bytes, *, precise: bool = False) -> Any:
    """Parse the body as strict JSON. ``precise`` keeps non-integer numbers as exact ``Decimal`` values.

    Integers are always Python ``int`` (arbitrary precision). NaN and Infinity are refused.
    """
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_reject_constant, parse_float=Decimal if precise else None)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ConnectorExecutionError(
            ErrorCategory.MALFORMED_RESPONSE, "The company API returned invalid JSON.", reason="invalid_json"
        ) from None


def _malformed(message: str) -> ConnectorExecutionError:
    return ConnectorExecutionError(ErrorCategory.MALFORMED_RESPONSE, message)


def _safe_error_code(error: Any) -> str | None:
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and 0 < len(code) <= 64 and set(code) <= _ERROR_CODE_CHARS:
        return code
    return None


def _rows(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(value, list):
        raise _malformed("The company API's result rows are not a JSON array.")
    columns: dict[str, None] = {}
    for row in value:
        if not isinstance(row, dict):
            raise _malformed("Every result row from the company API must be a JSON object.")
        for key in row:
            columns.setdefault(key, None)
    return list(columns), value


def normalize(document: Any, contract: ResponseContract, *, http_status: int, size: int) -> NormalizedResult:
    execution_time: float | None = None
    metadata: dict[str, Any] | None = None
    if contract.format == "raw_array":
        columns, rows = _rows(document)
    else:
        if not isinstance(document, dict):
            raise _malformed("The company API's response is not a JSON object.")
        if contract.success_field not in document:
            raise _malformed(f"The company API's response has no '{contract.success_field}' field.")
        success = document[contract.success_field]
        if not isinstance(success, bool):
            raise _malformed(f"The company API's '{contract.success_field}' field is not true or false.")
        if not success:
            code = _safe_error_code(document.get(contract.error_field))
            suffix = f" (error code {code})" if code else ""
            raise ConnectorExecutionError(
                ErrorCategory.EXTERNAL_API_ERROR, f"The company API reported that the query failed{suffix}.", http_status=http_status
            )
        if contract.data_field not in document or document[contract.data_field] is None:
            raise _malformed(f"The company API reported success but returned no '{contract.data_field}' field.")
        columns, rows = _rows(document[contract.data_field])
        metadata = document.get(contract.metadata_field)
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise _malformed(f"The company API's '{contract.metadata_field}' field is not a JSON object.")
            elapsed = metadata.get("execution_time_ms")
            if isinstance(elapsed, (int, float, Decimal)) and not isinstance(elapsed, bool) and elapsed >= 0:
                execution_time = float(elapsed)
    return NormalizedResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        execution_time_ms=execution_time,
        response_size_bytes=size,
        http_status=http_status,
        metadata=metadata,
    )


def check_health_document(document: Any, contract: ResponseContract) -> None:
    """A health endpoint passes with any JSON document, unless it is an envelope that says it failed."""
    if isinstance(document, dict) and contract.format == "envelope":
        success = document.get(contract.success_field)
        if success is False:
            raise ConnectorExecutionError(ErrorCategory.EXTERNAL_API_ERROR, "The company API's health check reported a failure.")
