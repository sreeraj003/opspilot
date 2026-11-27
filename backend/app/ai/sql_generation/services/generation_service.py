"""``SQLGenerationService``: validated ``BusinessIntent`` -> untrusted candidate SQL.

Workflow (only step 7 is non-deterministic):

1. Receive a validated BusinessIntent. Through the API it is always the stored
   intent that Module 09 produced for this user (``business_intents``), never
   an object sent by a client; it must be understood (not awaiting
   clarification), recent and for the same schema version.
2. Retrieve the registered schema (Module 06) and reject unsupported dialects.
3. Retrieve active business terms (Module 09).
4. Retrieve the user's *current* effective permissions (Module 04) and
   re-authorize everything the intent needs with the permission engine.
5. Build the constrained context (``context.builder``): intent tables, join
   paths over permitted tables, visible columns, terms, permission context.
6. Build the prompt (``prompts.sql_prompt``, versioned).
7. Ask the model through Module 08's ``LLMService`` (JSON only, temperature 0).
8. Parse the reply strictly (``LLMSQLOutput``).
9. Validate the SQL with SQLGlot (``validators.sql_validator``) against the
   context. Correctable problems get at most ``AI_SQL_MAX_REGENERATIONS`` more
   model requests with backend-written feedback; the context is never widened
   and the intent never changes. Unsafe SQL is never regenerated.
10. Authorize the tables and columns the SQL *actually* references (parsed,
    not the model's claims) with the permission engine, decide whether the
    user may see SQL text (``allow_sql_view``), persist the untrusted artifact
    and an audit event, and return it.

The service never executes SQL, never calls the company connector and never
approves a query: every result is ``awaiting_security_validation``.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import sqlglot
from pydantic import ValidationError
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions.errors import ProviderError
from app.ai.services.llm_service import LLMService
from app.ai.sql_generation.context.builder import (
    ContextPermissionError,
    ContextSchemaError,
    SQLGenerationContext,
    build_context,
    intent_references,
)
from app.ai.sql_generation.dialects.registry import SQLDialect, dialect_by_key, dialect_for_database_type
from app.ai.sql_generation.normalizers.sql_output import MalformedOutputError, extract_reply_json, sql_fingerprint
from app.ai.sql_generation.prompts.sql_prompt import PROMPT_VERSION, build_messages, feedback_messages
from app.ai.sql_generation.schemas.api import QuestionSummary, SQLGenerationView
from app.ai.sql_generation.schemas.result import (
    FailureCategory,
    GenerationStatus,
    JoinUsed,
    LLMSQLOutput,
    PreliminaryValidationStatus,
    SQLGenerationResult,
    ValidationIssueView,
)
from app.ai.sql_generation.validators.sql_validator import PreliminaryResult, PreliminarySQLValidator, SQLIssue
from app.ai.understanding.schemas.intent import AggregationType, BusinessIntent
from app.ai.understanding.services.permission_view import PermissionView, authorize_references
from app.ai.understanding.services.schema_context import SchemaContext
from app.ai.understanding.services.terminology_service import TerminologyService
from app.ai.understanding.services.understanding_service import SchemaUnavailableError
from app.authorization.engine import AuthorizationEngine
from app.core.config import Settings
from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.core.logging import get_request_id
from app.database.transaction import transaction
from app.models.schema_registry import SchemaSource
from app.models.sql_generation import BusinessIntentRecord, SQLGenerationEvent, SQLGenerationRecord
from app.models.user import User

logger = logging.getLogger("app.ai.sql_generation")

GENERATION_TEMPERATURE = 0.0
DENIED_EXPLANATION = "The question needs data you are not permitted to query, so no SQL was generated."
SQL_HIDDEN_NOTE = "Your data permissions do not allow viewing SQL text for these tables."
ROW_RESTRICTION_NOTE = (
    "Row-level restrictions apply to {table}. The SQL security guard enforces them before anything is executed."
)


# --- errors ------------------------------------------------------------------------------------------------


class IntentNotFoundError(NotFoundError):
    code = "INTENT_NOT_FOUND"
    message = "The interpreted question was not found. Ask the question again."


class IntentNotReadyError(ConflictError):
    code = "INTENT_NOT_READY"
    message = "The question still needs clarification before SQL can be generated."


class IntentExpiredError(ConflictError):
    code = "INTENT_EXPIRED"
    message = "The interpreted question is too old to generate SQL from. Ask the question again."


class IntentStaleError(ConflictError):
    code = "INTENT_SCHEMA_CHANGED"
    message = "The schema changed after the question was interpreted. Ask the question again."


class IntentMismatchError(InvalidInputError):
    code = "INTENT_SCHEMA_MISMATCH"
    message = "The interpreted question belongs to a different schema."


class IntentInvalidError(InvalidInputError):
    code = "INTENT_INVALID"
    message = "The business intent cannot be used for SQL generation."


class GenerationNotFoundError(NotFoundError):
    code = "SQL_GENERATION_NOT_FOUND"
    message = "The SQL generation was not found."


class RegenerationLimitError(ConflictError):
    code = "SQL_REGENERATION_LIMIT"
    message = "This question has been regenerated as often as allowed. Ask the question again to start over."


# --- run state ---------------------------------------------------------------------------------------------


@dataclass
class _Run:
    """Mutable state of one generation, for the record, the audit row and the log."""

    generation_id: uuid.UUID
    started: float
    intent: BusinessIntent
    schema_source_id: uuid.UUID
    regenerated_from: uuid.UUID | None = None
    schema: SchemaContext | None = None
    dialect: SQLDialect | None = None
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    prompt_chars: int = 0
    issues: list[SQLIssue] = field(default_factory=list)
    failure: FailureCategory | None = None
    validation: PreliminaryValidationStatus = PreliminaryValidationStatus.NOT_RUN
    policy_revision: int = 0
    # The full normalized SQL, stored even when the user may not view it.
    sql: str | None = None


@dataclass
class _Outcome:
    status: GenerationStatus
    output: LLMSQLOutput | None = None
    preliminary: PreliminaryResult | None = None


class SQLGenerationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        llm: LLMService,
        terminology: TerminologyService,
        engine: AuthorizationEngine,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._settings = settings
        self._llm = llm
        self._terminology = terminology
        self._engine = engine
        self._clock = clock

    # =========================================================================================
    # API entry points
    # =========================================================================================

    async def generate(self, user: User, intent_id: uuid.UUID, schema_source_id: uuid.UUID) -> SQLGenerationView:
        record = await self._intent_record(user, intent_id)
        intent = self._usable_intent(record)
        result = await self.generate_sql(intent, user.id, schema_source_id)
        return await self._view(user, result, record)

    async def regenerate(self, user: User, generation_id: uuid.UUID) -> SQLGenerationView:
        previous = await self._generation(user, generation_id)
        record = await self._intent_record(user, previous.intent_id)
        intent = self._usable_intent(record)
        if await self._regenerations_used(intent.intent_id) >= self._settings.ai_sql_max_manual_regenerations:
            raise RegenerationLimitError()
        if previous.schema_source_id is None:
            raise GenerationNotFoundError()
        result = await self.generate_sql(
            intent, user.id, previous.schema_source_id, regenerated_from=previous.id
        )
        return await self._view(user, result, record)

    async def get(self, user: User, generation_id: uuid.UUID) -> SQLGenerationView:
        record = await self._generation(user, generation_id)
        intent_record = await self._session.get(BusinessIntentRecord, record.intent_id)
        result = self._result_from_record(record)
        if result.status == GenerationStatus.GENERATED:
            result = await self._with_sql_visibility(user.id, result, record.generated_sql)
        return await self._view(user, result, intent_record if intent_record and intent_record.user_id == user.id else None,
                                created_at=record.created_at)

    # =========================================================================================
    # the core
    # =========================================================================================

    async def generate_sql(
        self,
        business_intent: BusinessIntent,
        user_id: uuid.UUID,
        schema_source_id: uuid.UUID,
        *,
        regenerated_from: uuid.UUID | None = None,
    ) -> SQLGenerationResult:
        """Generate one untrusted candidate query for a validated intent. Never executes it."""
        run = _Run(
            generation_id=uuid.uuid4(), started=time.perf_counter(), intent=business_intent,
            schema_source_id=schema_source_id, regenerated_from=regenerated_from,
        )
        user = await self._session.get(User, user_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            raise IntentNotFoundError()
        try:
            result = await self._generate(user, run)
        except ProviderError as exc:
            run.failure = FailureCategory.PROVIDER_ERROR
            await self._audit(user, run, GenerationStatus.GENERATION_FAILED, error_type=exc.category.value)
            self._log(run, GenerationStatus.GENERATION_FAILED)
            raise
        await self._persist(user, run, result)
        await self._audit(user, run, result.status)
        self._log(run, result.status)
        return result

    async def _generate(self, user: User, run: _Run) -> SQLGenerationResult:
        intent = run.intent
        # 1. the intent
        self._check_intent(intent, run.schema_source_id)

        # 2. schema and dialect
        schema = await self._terminology.schema_context(run.schema_source_id)
        run.schema = schema
        if not schema.is_active or not schema.tables:
            raise SchemaUnavailableError()
        if schema.schema_version != intent.schema_version:
            raise IntentStaleError()
        run.dialect = dialect_for_database_type(schema.database_type)

        # 3-4. terms and current permissions (backend engine, never the model or the stored intent)
        terms = await self._terminology.active_terms(schema.source_id)
        permissions = PermissionView(await self._engine.ai_authorization_context(user), schema)
        run.policy_revision = permissions.policy_revision
        if not permissions.has_any_data_access:
            return self._denied(run)
        needed = intent_references(intent, terms)
        try:
            context = build_context(
                intent, schema, permissions, terms, run.dialect,
                max_tables=self._settings.ai_sql_max_schema_tables, max_row_limit=self._settings.ai_sql_max_row_limit,
            )
        except ContextPermissionError:
            return self._denied(run)
        except ContextSchemaError:
            raise IntentStaleError() from None
        operation = "aggregate" if any(m.aggregation != AggregationType.NONE for m in intent.requested_metrics) else "read"
        if await authorize_references(self._engine, user.id, permissions, _registry_spelling(needed, schema), operation) is None:
            return self._denied(run)

        # 5-9. prompt, model, parse, validate (bounded regeneration)
        outcome = await self._ask(user, run, context)
        if outcome.status == GenerationStatus.CLARIFICATION_REQUIRED:
            assert outcome.output is not None
            return self._clarification(run, outcome.output)
        if outcome.status == GenerationStatus.GENERATION_FAILED:
            return self._failed(run, outcome.output)
        assert outcome.output is not None and outcome.preliminary is not None
        preliminary = outcome.preliminary

        # 10. independent authorization of what the SQL actually references
        sql_operation = "aggregate" if preliminary.aggregating else "read"
        referenced = preliminary.referenced_map()
        decision = await authorize_references(self._engine, user.id, permissions, referenced, sql_operation)
        if decision is None:
            run.issues = [SQLIssue("restricted_reference", "The query references data you are not permitted to query.", True)]
            return self._denied(run, validation=PreliminaryValidationStatus.FAILED)
        visible = await authorize_references(self._engine, user.id, permissions, referenced, sql_operation, sql_view=True)
        return self._generated(run, outcome.output, preliminary, context, sql_visible=visible is not None)

    # --- the model -------------------------------------------------------------------------------------

    async def _ask(self, user: User, run: _Run, context: SQLGenerationContext) -> _Outcome:
        messages = build_messages(context.payload())
        run.prompt_chars = sum(len(m["content"]) for m in messages)
        validator = PreliminarySQLValidator(context)
        conversation = messages
        max_attempts = 1 + self._settings.ai_sql_max_regenerations
        while True:
            response = await self._llm.generate(
                conversation,
                temperature=GENERATION_TEMPERATURE,
                max_tokens=self._settings.ai_sql_max_output_tokens,
                response_format="json_object",
                operation="sql_generation",
                metadata={"feature": "sql_generation", "attempt": run.attempts + 1},
                user_id=user.id,
            )
            run.attempts += 1
            run.provider, run.model = response.provider, response.model
            output, issues = self._parse(response.content)
            preliminary: PreliminaryResult | None = None
            if output is not None:
                if output.status == "clarification_required":
                    run.issues = []
                    return _Outcome(GenerationStatus.CLARIFICATION_REQUIRED, output)
                issues = self._declared_reference_issues(output, context)
                assert output.sql is not None
                preliminary = validator.validate(output.sql)
                issues = [*preliminary.issues, *issues]
                run.validation = PreliminaryValidationStatus.PASSED if not issues else PreliminaryValidationStatus.FAILED
                if not issues:
                    run.issues = []
                    return _Outcome(GenerationStatus.GENERATED, output, preliminary)
                if preliminary.unsafe:
                    run.issues = issues
                    run.failure = FailureCategory.UNSAFE_SQL
                    return _Outcome(GenerationStatus.GENERATION_FAILED, output, preliminary)
            run.issues = issues
            if run.attempts >= max_attempts:
                run.failure = FailureCategory.MALFORMED_OUTPUT if output is None else FailureCategory.VALIDATION_FAILED
                return _Outcome(GenerationStatus.GENERATION_FAILED, output, preliminary)
            logger.info(
                "Requesting regenerated SQL",
                extra={"event": "ai.sql_generation.regenerate", "generation_id": str(run.generation_id),
                       "issue_codes": sorted({i.code for i in issues}), "attempt": run.attempts + 1},
            )
            # Only our own messages; the context (and so the permitted schema) stays exactly the same.
            conversation = feedback_messages(messages, response.content, [i.message for i in issues])

    @staticmethod
    def _parse(content: str) -> tuple[LLMSQLOutput | None, list[SQLIssue]]:
        """(output, issues). Never echoes the reply's text into an issue."""
        try:
            text = extract_reply_json(content)
        except MalformedOutputError as exc:
            return None, [SQLIssue("malformed_json", str(exc))]
        try:
            return LLMSQLOutput.model_validate_json(text), []
        except ValidationError as exc:
            issues: list[SQLIssue] = []
            for error in exc.errors(include_input=False, include_url=False)[:10]:
                message = str(error.get("msg", "invalid value"))
                location = ".".join(str(p) for p in error.get("loc", ())) or "reply"
                if "SQL fragment" in message or "code or tool" in message or "control characters" in message:
                    issues.append(
                        SQLIssue("executable_content_in_text", f"'{location}' must be plain sentences without SQL or code.")
                    )
                elif error.get("type") == "json_invalid":
                    issues.append(SQLIssue("malformed_json", "The reply was not valid JSON."))
                elif error.get("type") == "extra_forbidden":
                    issues.append(SQLIssue("unknown_field", f"Field '{location}' is not part of the contract."))
                else:
                    issues.append(SQLIssue("invalid_structure", f"{location}: {message}"))
            return None, issues

    @staticmethod
    def _declared_reference_issues(output: LLMSQLOutput, context: SQLGenerationContext) -> list[SQLIssue]:
        """The model's own reference lists must name supplied metadata (they are never trusted otherwise)."""
        issues: list[SQLIssue] = []
        for table in output.referenced_tables:
            if context.table(table) is None:
                issues.append(SQLIssue("declared_unknown_table", f"referenced_tables lists '{table}', which was not supplied."))
        for column in [*output.referenced_columns, *(j.from_column for j in output.joins_used),
                       *(j.to_column for j in output.joins_used)]:
            table, name = column.rsplit(".", 1)
            if not context.column_visible(table, name):
                issues.append(SQLIssue("declared_unknown_column", f"'{column}' is not one of the supplied columns."))
        return list(dict.fromkeys(issues))

    # --- results ---------------------------------------------------------------------------------------

    def _base(self, run: _Run) -> dict[str, object]:
        dialect = run.dialect
        return {
            "generation_id": run.generation_id,
            "dialect": dialect.key if dialect else "postgres",
            "dialect_name": dialect.display_name if dialect else "",
            "generation_model": run.model,
            "provider": run.provider,
            "schema_source_id": run.schema_source_id,
            "schema_version": run.intent.schema_version,
            "intent_id": run.intent.intent_id,
            "validation_status": run.validation,
            "validation_issues": [ValidationIssueView(**i.as_dict()) for i in run.issues],  # type: ignore[arg-type]
            "generation_attempts": run.attempts,
            "prompt_version": PROMPT_VERSION,
            "regenerated_from_id": run.regenerated_from,
            "latency_ms": _elapsed(run),
        }

    def _generated(
        self, run: _Run, output: LLMSQLOutput, preliminary: PreliminaryResult, context: SQLGenerationContext,
        *, sql_visible: bool,
    ) -> SQLGenerationResult:
        sql = preliminary.sql or ""
        notes = [
            ROW_RESTRICTION_NOTE.format(table=t)
            for t in preliminary.referenced_tables
            if context.permissions.row_filter(t) is not None
        ]
        if not sql_visible:
            notes.append(SQL_HIDDEN_NOTE)
        if context.truncated:
            notes.append("Some join paths were left out of the context because of the table limit.")
        run.sql = sql
        return SQLGenerationResult(
            **self._base(run),  # type: ignore[arg-type]
            status=GenerationStatus.GENERATED,
            sql=sql if sql_visible else None,
            sql_visible=sql_visible,
            sql_sha256=_sha256(sql),
            referenced_tables=preliminary.referenced_tables,
            referenced_columns=preliminary.referenced_columns,
            joins_used=preliminary.joins,
            assumptions=list(dict.fromkeys(output.assumptions)),
            explanation=output.explanation,
            permission_notes=notes,
        )

    def _clarification(self, run: _Run, output: LLMSQLOutput) -> SQLGenerationResult:
        return SQLGenerationResult(
            **self._base(run),  # type: ignore[arg-type]
            status=GenerationStatus.CLARIFICATION_REQUIRED,
            clarification_required=True,
            clarification_question=output.clarification_question,
            assumptions=list(output.assumptions),
            explanation=output.explanation or "More information is needed before SQL can be generated.",
        )

    def _failed(self, run: _Run, output: LLMSQLOutput | None) -> SQLGenerationResult:
        if run.failure == FailureCategory.UNSAFE_SQL:
            explanation = "The generated query was not a single read-only SELECT statement, so it was discarded."
        elif run.failure == FailureCategory.MALFORMED_OUTPUT:
            explanation = "The AI reply could not be read as a structured result. Try regenerating."
        else:
            explanation = (
                "The generated query did not pass preliminary validation against the registered schema after "
                f"{run.attempts} attempt{'s' if run.attempts != 1 else ''}. Try regenerating or rephrasing the question."
            )
        return SQLGenerationResult(**self._base(run), status=GenerationStatus.GENERATION_FAILED,  # type: ignore[arg-type]
                                   failure_category=run.failure, explanation=explanation)

    def _denied(self, run: _Run, *, validation: PreliminaryValidationStatus | None = None) -> SQLGenerationResult:
        run.failure = FailureCategory.PERMISSION_DENIED
        if validation is not None:
            run.validation = validation
        return SQLGenerationResult(
            **self._base(run),  # type: ignore[arg-type]
            status=GenerationStatus.GENERATION_FAILED,
            failure_category=FailureCategory.PERMISSION_DENIED,
            explanation=DENIED_EXPLANATION,
            permission_notes=[DENIED_EXPLANATION],
        )

    # --- intents and records ---------------------------------------------------------------------------

    def _check_intent(self, intent: BusinessIntent, schema_source_id: uuid.UUID) -> None:
        if intent.schema_source_id != schema_source_id:
            raise IntentMismatchError()
        if intent.requires_clarification:
            raise IntentNotReadyError()
        if intent.intent_type.value == "unknown":
            raise IntentInvalidError("The question was not understood well enough to generate SQL.")
        if intent.permission_context.status != "allowed":
            raise IntentInvalidError("The question was not permitted when it was interpreted.")
        if not (intent.requested_metrics or intent.requested_dimensions or intent.requested_attributes):
            raise IntentInvalidError("The interpreted question names no measure, grouping or attribute to select.")

    async def _intent_record(self, user: User, intent_id: uuid.UUID) -> BusinessIntentRecord:
        record = await self._session.get(BusinessIntentRecord, intent_id)
        if record is None or record.user_id != user.id:
            raise IntentNotFoundError()
        return record

    def _usable_intent(self, record: BusinessIntentRecord) -> BusinessIntent:
        if record.status != "understood":
            raise IntentNotReadyError()
        created = record.created_at if record.created_at.tzinfo else record.created_at.replace(tzinfo=UTC)
        if self._clock() - created > timedelta(minutes=self._settings.ai_sql_intent_max_age_minutes):
            raise IntentExpiredError()
        try:
            return BusinessIntent.model_validate(record.intent)
        except ValidationError:
            raise IntentInvalidError() from None

    async def _generation(self, user: User, generation_id: uuid.UUID) -> SQLGenerationRecord:
        record = await self._session.get(SQLGenerationRecord, generation_id)
        if record is None or record.user_id != user.id:
            raise GenerationNotFoundError()
        return record

    async def _regenerations_used(self, intent_id: uuid.UUID) -> int:
        statement = select(func.count()).select_from(SQLGenerationRecord).where(
            SQLGenerationRecord.intent_id == intent_id, SQLGenerationRecord.regenerated_from_id.is_not(None)
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def _persist(self, user: User, run: _Run, result: SQLGenerationResult) -> None:
        """Store the untrusted artifact. The full SQL is stored even when the user may not view it."""
        sql = run.sql if result.status == GenerationStatus.GENERATED else None
        intent_exists = await self._session.get(BusinessIntentRecord, result.intent_id) is not None
        async with transaction(self._session):
            self._session.add(
                SQLGenerationRecord(
                    id=result.generation_id,
                    user_id=user.id,
                    schema_source_id=result.schema_source_id,
                    schema_version=result.schema_version,
                    intent_id=result.intent_id,
                    intent_record_id=result.intent_id if intent_exists else None,
                    regenerated_from_id=result.regenerated_from_id,
                    dialect=result.dialect,
                    generated_sql=sql,
                    sql_sha256=_sha256(sql) if sql else None,
                    generation_status=result.status.value,
                    validation_status=result.validation_status.value,
                    failure_category=result.failure_category.value if result.failure_category else None,
                    provider=result.provider,
                    model_name=result.generation_model,
                    prompt_version=result.prompt_version,
                    generation_attempts=result.generation_attempts,
                    latency_ms=result.latency_ms,
                    policy_revision=run.policy_revision,
                    referenced_tables=result.referenced_tables,
                    referenced_columns=result.referenced_columns,
                    joins_used=[j.model_dump() for j in result.joins_used],
                    assumptions=result.assumptions,
                    explanation=result.explanation,
                    clarification_question=result.clarification_question,
                    permission_notes=[n for n in result.permission_notes if n != SQL_HIDDEN_NOTE],
                    validation_issues=[i.model_dump() for i in result.validation_issues],
                )
            )

    def _result_from_record(self, record: SQLGenerationRecord) -> SQLGenerationResult:
        dialect = dialect_by_key(record.dialect)
        status = GenerationStatus(record.generation_status)
        return SQLGenerationResult(
            generation_id=record.id,
            status=status,
            sql=None,
            dialect=dialect.key,
            dialect_name=dialect.display_name,
            referenced_tables=list(record.referenced_tables),
            referenced_columns=list(record.referenced_columns),
            joins_used=[JoinUsed(**j) for j in record.joins_used],
            assumptions=list(record.assumptions),
            explanation=record.explanation,
            clarification_required=status == GenerationStatus.CLARIFICATION_REQUIRED,
            clarification_question=record.clarification_question,
            permission_notes=list(record.permission_notes),
            generation_model=record.model_name,
            provider=record.provider,
            schema_source_id=record.schema_source_id,
            schema_version=record.schema_version,
            intent_id=record.intent_id,
            validation_status=PreliminaryValidationStatus(record.validation_status),
            validation_issues=[ValidationIssueView(**i) for i in record.validation_issues],
            failure_category=FailureCategory(record.failure_category) if record.failure_category else None,
            generation_attempts=record.generation_attempts,
            prompt_version=record.prompt_version,
            regenerated_from_id=record.regenerated_from_id,
            sql_sha256=record.sql_sha256,
            latency_ms=record.latency_ms,
        )

    async def _with_sql_visibility(self, user_id: uuid.UUID, result: SQLGenerationResult, sql: str | None) -> SQLGenerationResult:
        """Show the stored SQL only if the user's *current* permissions allow viewing it."""
        user = await self._session.get(User, user_id)
        if user is None or sql is None or result.schema_source_id is None:
            return result
        try:
            schema = await self._terminology.schema_context(result.schema_source_id)
        except NotFoundError:
            return result
        permissions = PermissionView(await self._engine.ai_authorization_context(user), schema)
        referenced: dict[str, set[str]] = {t: set() for t in result.referenced_tables}
        for column in result.referenced_columns:
            table, name = column.rsplit(".", 1)
            referenced.setdefault(table, set()).add(name)
        visible = all(permissions.table_allowed(t) for t in referenced) and await authorize_references(
            self._engine, user.id, permissions, referenced, _operation(sql, result.dialect), sql_view=True
        ) is not None
        notes = [n for n in result.permission_notes if n != SQL_HIDDEN_NOTE]
        if not visible:
            notes.append(SQL_HIDDEN_NOTE)
        return result.model_copy(update={"sql": sql if visible else None, "sql_visible": visible, "permission_notes": notes})

    async def _view(
        self, user: User, result: SQLGenerationResult, record: BusinessIntentRecord | None, *, created_at: datetime | None = None
    ) -> SQLGenerationView:
        question: QuestionSummary | None = None
        remaining = 0
        if record is not None:
            try:
                intent = BusinessIntent.model_validate(record.intent)
            except ValidationError:
                intent = None
            if intent is not None:
                question = _summary(intent, await self._schema_name(record.schema_source_id))
                used = await self._regenerations_used(intent.intent_id)
                remaining = max(0, self._settings.ai_sql_max_manual_regenerations - used)
        return SQLGenerationView(
            **result.model_dump(), question=question, regenerations_remaining=remaining,
            created_at=(created_at or self._clock()).isoformat(),
        )

    async def _schema_name(self, schema_source_id: uuid.UUID) -> str | None:
        source = await self._session.get(SchemaSource, schema_source_id)
        return source.name if source else None

    # --- audit and logs ----------------------------------------------------------------------------------

    async def _audit(self, user: User, run: _Run, status: GenerationStatus, *, error_type: str | None = None) -> None:
        """One audit row per request: ids and outcome only. A storage failure is logged, never raised."""
        try:
            async with transaction(self._session):
                self._session.add(
                    SQLGenerationEvent(
                        request_id=get_request_id(),
                        generation_id=run.generation_id if run.failure != FailureCategory.PROVIDER_ERROR else None,
                        user_id=user.id,
                        intent_id=run.intent.intent_id,
                        schema_source_id=run.schema_source_id if run.schema else None,
                        schema_version=run.intent.schema_version,
                        dialect=run.dialect.key if run.dialect else None,
                        is_regeneration=run.regenerated_from is not None,
                        provider=run.provider,
                        model=run.model,
                        latency_ms=_elapsed(run),
                        retry_count=max(0, run.attempts - 1),
                        status=status.value,
                        validation_outcome=run.validation.value,
                        failure_category=run.failure.value if run.failure else None,
                        details={
                            "issue_codes": sorted({i.code for i in run.issues}),
                            "attempts": run.attempts,
                            "prompt_version": PROMPT_VERSION,
                            **({"provider_error": error_type} if error_type else {}),
                        },
                    )
                )
        except SQLAlchemyError as exc:
            logger.error(
                "SQL generation audit could not be stored",
                extra={"event": "ai.sql_generation.audit_failed", "error_type": type(exc).__name__},
            )

    def _log(self, run: _Run, status: GenerationStatus) -> None:
        logger.info(
            "SQL generated",
            extra={
                "event": "ai.sql_generation.completed",
                "generation_id": str(run.generation_id),
                "intent_id": str(run.intent.intent_id),
                "schema_source_id": str(run.schema_source_id),
                "schema_version": run.intent.schema_version,
                "dialect": run.dialect.key if run.dialect else None,
                "provider": run.provider,
                "model": run.model,
                "latency_ms": _elapsed(run),
                "attempts": run.attempts,
                "retry_count": max(0, run.attempts - 1),
                "status": status.value,
                "validation_outcome": run.validation.value,
                "failure_category": run.failure.value if run.failure else None,
                "issue_codes": sorted({i.code for i in run.issues}),
                "regeneration": run.regenerated_from is not None,
                "prompt_chars": run.prompt_chars,
            },
        )


# --- helpers ----------------------------------------------------------------------------------------------


def _elapsed(run: _Run) -> int:
    return max(0, round((time.perf_counter() - run.started) * 1000))


def _sha256(sql: str | None) -> str | None:
    return sql_fingerprint(sql) if sql else None


def _operation(sql: str, dialect_key: str) -> str:
    """The permission operation a stored query needs: ``aggregate`` when it aggregates, else ``read``."""
    try:
        statement = sqlglot.parse_one(sql, read=dialect_by_key(dialect_key).sqlglot_dialect)
    except SqlglotError:
        return "read"
    return "aggregate" if statement.find(exp.AggFunc) is not None else "read"


def _registry_spelling(needed: dict[str, set[str]], schema: SchemaContext) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for key, columns in needed.items():
        table = schema.table(key)
        if table is None:
            continue
        result[table.qualified] = {table.column(c).name if table.column(c) else c for c in columns}  # type: ignore[union-attr]
    return result


def _summary(intent: BusinessIntent, schema_name: str | None) -> QuestionSummary:
    time_range = intent.time.time_range
    return QuestionSummary(
        original_question=intent.original_question,
        normalized_question=intent.normalized_question,
        intent_type=intent.intent_type.value,
        explanation=_intent_explanation(intent),
        schema_source_id=intent.schema_source_id,
        schema_name=schema_name,
        metrics=[f"{m.name} ({m.aggregation.value})" for m in intent.requested_metrics],
        dimensions=[d.name for d in intent.requested_dimensions],
        filters=[f"{f.field.qualified} {f.operator.value.replace('_', ' ')} {f.value}" for f in intent.filters],
        time_range=(f"{time_range.label} ({time_range.start_date} to before {time_range.end_date})" if time_range else None),
        limit=intent.aggregation.limit,
        assumptions=list(intent.assumptions),
    )


def _intent_explanation(intent: BusinessIntent) -> str:
    parts = [intent.intent_type.value.replace("_", " ").capitalize()]
    if intent.requested_metrics:
        parts.append("of " + ", ".join(m.name for m in intent.requested_metrics))
    if intent.requested_dimensions:
        parts.append("by " + ", ".join(d.name for d in intent.requested_dimensions))
    if intent.time.time_range:
        parts.append(f"for {intent.time.time_range.label.lower()}")
    return " ".join(parts) + "."
