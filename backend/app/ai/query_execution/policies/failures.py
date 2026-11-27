"""Execution failure categories, their safe messages, and how guard and connector failures map to them.

Every unsuccessful execution ends with exactly one ``ExecutionErrorCategory``
and the safe message below. Internal detail (the guard's reason code, the
connector's category, the HTTP status) goes only to the audit log.

Statuses:

* ``rejected``: nothing was sent (the approval or its context did not pass).
* ``failed``: either nothing was sent, or the company API answered with a
  definite failure (an HTTP error, ``success: false``, an unusable body).
* ``timed_out``: the request may have reached the company API, but no answer
  came in time. The outcome is unknown; it is never retried automatically.
* ``outcome_unknown``: the request may have reached the company API, but the
  connection was lost before a complete answer arrived. Never retried
  automatically; a new run needs a new validation and approval.
"""

from enum import StrEnum

from app.connectors.errors import ConnectorExecutionError, ErrorCategory


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (ExecutionStatus.PENDING, ExecutionStatus.RUNNING)
UNCERTAIN_STATUSES = (ExecutionStatus.TIMED_OUT, ExecutionStatus.OUTCOME_UNKNOWN)


class ExecutionErrorCategory(StrEnum):
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CONSUMED = "approval_consumed"
    APPROVAL_REVOKED = "approval_revoked"
    APPROVAL_INVALIDATED = "approval_invalidated"
    SQL_MISMATCH = "sql_mismatch"
    PERMISSION_CHANGED = "permission_changed"
    SCHEMA_CHANGED = "schema_changed"
    CONNECTOR_INACTIVE = "connector_inactive"
    CONNECTOR_MISCONFIGURED = "connector_misconfigured"
    CONNECTOR_AUTH_FAILED = "connector_auth_failed"
    DESTINATION_REJECTED = "destination_rejected"
    EXTERNAL_API_UNAVAILABLE = "external_api_unavailable"
    EXTERNAL_API_TIMEOUT = "external_api_timeout"
    EXTERNAL_API_ERROR = "external_api_error"
    URL_TOO_LONG = "url_too_long"
    INVALID_JSON = "invalid_json"
    INVALID_RESPONSE_STRUCTURE = "invalid_response_structure"
    RESPONSE_TOO_LARGE = "response_too_large"
    NORMALIZATION_FAILED = "normalization_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CONCURRENCY_LIMIT = "concurrency_limit"
    INTERNAL_ERROR = "internal_error"


C = ExecutionErrorCategory
SAFE_MESSAGES: dict[ExecutionErrorCategory, str] = {
    C.APPROVAL_NOT_FOUND: "The execution approval was not found.",
    C.APPROVAL_REJECTED: "This query is not approved for execution. Validate it again.",
    C.APPROVAL_EXPIRED: "The approval expired before the query was run. Validate the query again.",
    C.APPROVAL_CONSUMED: "This approval was already used. Each approval runs a query at most once; validate it again.",
    C.APPROVAL_REVOKED: "This approval was revoked. Validate the query again to run it.",
    C.APPROVAL_INVALIDATED: "The query no longer passes the security checks, so its approval was invalidated.",
    C.SQL_MISMATCH: "The query no longer matches the approved SQL. Validate it again.",
    C.PERMISSION_CHANGED: "Your data permissions changed after the query was approved. Validate it again.",
    C.SCHEMA_CHANGED: "The schema changed after the query was approved. Generate and validate the query again.",
    C.CONNECTOR_INACTIVE: "The company API connector for this schema is not active.",
    C.CONNECTOR_MISCONFIGURED: "The company API connector is not configured correctly. Ask an administrator.",
    C.CONNECTOR_AUTH_FAILED: "The company API rejected the connector's credentials. Ask an administrator.",
    C.DESTINATION_REJECTED: "The company API's address is not allowed. Ask an administrator.",
    C.EXTERNAL_API_UNAVAILABLE: "The company API could not be reached. The query was not run.",
    C.EXTERNAL_API_TIMEOUT: "The company API did not answer in time. It may still have run the query; it was not retried.",
    C.EXTERNAL_API_ERROR: "The company API reported that the query failed.",
    C.URL_TOO_LONG: "The query is too long to send to the company API as a GET request. It was not sent.",
    C.INVALID_JSON: "The company API returned a response that is not valid JSON.",
    C.INVALID_RESPONSE_STRUCTURE: "The company API returned a response with an unexpected structure.",
    C.RESPONSE_TOO_LARGE: "The company API's result is larger than this installation allows.",
    C.NORMALIZATION_FAILED: "The result contains values that could not be read reliably, so it is not shown.",
    C.OUTCOME_UNKNOWN: (
        "The connection to the company API was lost after the query was sent. It may have run; it was not retried. "
        "To run it again, validate the query again."
    ),
    C.CONCURRENCY_LIMIT: "You already have the maximum number of queries running. Wait for one to finish.",
    C.INTERNAL_ERROR: "The query could not be run because of an internal error.",
}


def message_for(category: ExecutionErrorCategory) -> str:
    return SAFE_MESSAGES[category]


_APPROVAL_STATUS_REASONS = {
    "approval_consumed": C.APPROVAL_CONSUMED,
    "approval_executed": C.APPROVAL_CONSUMED,
    "replay": C.APPROVAL_CONSUMED,
    "approval_revoked": C.APPROVAL_REVOKED,
    "approval_expired": C.APPROVAL_EXPIRED,
    "approval_invalidated": C.APPROVAL_INVALIDATED,
    "unknown_approval": C.APPROVAL_NOT_FOUND,
    "different_user": C.APPROVAL_NOT_FOUND,
    "different_connector": C.CONNECTOR_INACTIVE,
    "connector_changed": C.CONNECTOR_INACTIVE,
    "schema_version_changed": C.SCHEMA_CHANGED,
    "permissions_changed": C.PERMISSION_CHANGED,
    "sql_changed": C.SQL_MISMATCH,
    "sql_modified": C.SQL_MISMATCH,
}


def from_guard_reason(reason: str) -> ExecutionErrorCategory:
    """Map a ``SQLExecutionGuard`` refusal reason (internal) to a public category."""
    if reason in _APPROVAL_STATUS_REASONS:
        return _APPROVAL_STATUS_REASONS[reason]
    if reason.startswith("revalidation_"):
        _, _, guard_category = reason.partition(":")
        if guard_category in ("schema_changed", "schema_unavailable"):
            return C.SCHEMA_CHANGED
        if guard_category == "permission_denied":
            return C.PERMISSION_CHANGED
        if guard_category == "connector_unavailable":
            return C.CONNECTOR_INACTIVE
        if reason == "revalidation_unavailable":
            return C.APPROVAL_REJECTED
        return C.APPROVAL_INVALIDATED
    return C.APPROVAL_REJECTED


def from_connector_error(error: ConnectorExecutionError) -> tuple[ExecutionStatus, ExecutionErrorCategory]:
    """Map a Module 07 connector failure to an execution status and category.

    Anything that may have reached the company API without a complete answer is
    ``timed_out`` or ``outcome_unknown``, never ``failed`` (which would suggest it
    did not run) and never a success.
    """
    category = error.category
    if error.outcome_uncertain:
        if category == ErrorCategory.TIMEOUT:
            return ExecutionStatus.TIMED_OUT, C.EXTERNAL_API_TIMEOUT
        return ExecutionStatus.OUTCOME_UNKNOWN, C.OUTCOME_UNKNOWN
    failed = ExecutionStatus.FAILED
    match category:
        case ErrorCategory.VALIDATION_REJECTED:
            return ExecutionStatus.REJECTED, C.APPROVAL_REJECTED
        case ErrorCategory.REQUEST_TOO_LARGE:
            return failed, C.URL_TOO_LONG
        case ErrorCategory.AUTHENTICATION_FAILED:
            return failed, C.CONNECTOR_AUTH_FAILED
        case ErrorCategory.TIMEOUT:
            return failed, C.EXTERNAL_API_TIMEOUT
        case ErrorCategory.DESTINATION_REJECTED | ErrorCategory.REDIRECT_REJECTED:
            return failed, C.DESTINATION_REJECTED
        case ErrorCategory.DNS_RESOLUTION_FAILED | ErrorCategory.CONNECTION_FAILED | ErrorCategory.TLS_FAILED:
            return failed, C.EXTERNAL_API_UNAVAILABLE
        case ErrorCategory.HTTP_ERROR:
            status = error.http_status or 0
            return failed, (C.EXTERNAL_API_UNAVAILABLE if status >= 500 or status == 429 else C.EXTERNAL_API_ERROR)
        case ErrorCategory.EXTERNAL_API_ERROR:
            return failed, C.EXTERNAL_API_ERROR
        case ErrorCategory.RESPONSE_TOO_LARGE:
            return failed, C.RESPONSE_TOO_LARGE
        case ErrorCategory.INVALID_CONTENT_TYPE:
            return failed, C.INVALID_RESPONSE_STRUCTURE
        case ErrorCategory.MALFORMED_RESPONSE:
            return failed, (C.INVALID_JSON if error.reason == "invalid_json" else C.INVALID_RESPONSE_STRUCTURE)
        case ErrorCategory.ENCRYPTION_UNAVAILABLE | ErrorCategory.CONFIGURATION_INVALID:
            return failed, C.CONNECTOR_MISCONFIGURED
    return failed, C.INTERNAL_ERROR
