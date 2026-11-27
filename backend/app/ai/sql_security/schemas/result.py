"""The guard's structured decision (``SQLSecurityValidationResult``) and API views.

The result is produced only by the backend guard and stored append-only; the
API never accepts one as input, and the model never sees or edits it.
``validation_errors`` are safe, user-facing messages. Detailed internal
reasons are stored separately and returned only to holders of
``ai.sql.security.audit`` (``internal_reasons``).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ValidationStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CLARIFICATION_REQUIRED = "clarification_required"
    VALIDATION_FAILED = "validation_failed"
    APPROVAL_EXPIRED = "approval_expired"


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    CONSUMED = "consumed"
    EXECUTED = "executed"
    REVOKED = "revoked"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ValidationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class InternalReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    category: str
    reason: str


class SQLSecurityValidationResult(BaseModel):
    """One decision of the SQL Security and Execution Guard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_id: uuid.UUID
    generation_id: uuid.UUID
    approved: bool
    status: ValidationStatus
    validation_errors: list[ValidationMessage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)
    detected_functions: list[str] = Field(default_factory=list)
    # Tables whose row-level restrictions were injected (never the restriction values).
    applied_row_restrictions: list[str] = Field(default_factory=list)
    schema_version: int | None = None
    permission_version: int | None = None
    query_hash: str | None = None
    validated_at: datetime


class ApprovalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: uuid.UUID
    validation_id: uuid.UUID
    generation_id: uuid.UUID
    connector_id: uuid.UUID
    schema_source_id: uuid.UUID
    schema_version: int
    permission_version: int
    exact_sql_hash: str
    dialect: str
    status: ApprovalStatus
    approved_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    # Seconds left before expiry (0 once it is no longer usable).
    expires_in_seconds: int = 0
    is_usable: bool = False


class ValidationView(SQLSecurityValidationResult):
    """API view: the result plus the safe summary, approval state and (for auditors) internal reasons."""

    rejection_category: str | None = None
    summary: str
    dialect: str | None = None
    connector_id: uuid.UUID | None = None
    # The exact SQL that would be executed, only with an allow_sql_view data permission.
    approved_sql: str | None = None
    sql_visible: bool = False
    approval: ApprovalView | None = None
    # True while the approval can still be executed once (Module 12: POST /ai/sql/approvals/{id}/execute).
    execution_available: bool = False
    internal_reasons: list[InternalReason] | None = None


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional: the connector to bind the approval to. Defaults to the active connector of the schema.
    connector_id: uuid.UUID | None = None
