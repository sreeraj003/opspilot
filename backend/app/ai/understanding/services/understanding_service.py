"""``QuestionUnderstandingService``: question -> validated ``BusinessIntent``.

Pipeline (each step is deterministic backend code except step 6):

1. Validate the request (plain-language question, no SQL statements).
2. Load the registered schema (Module 06) and the business timezone.
3. Load the user's effective data permissions (Module 04). No data access at
   all ends here, before any model call.
4. Match business terms. A term the user may not use (its fields are
   restricted) ends with a safe denial.
5. Select the relevant tables the user may see (deterministic keyword
   relevance) and build the constrained prompt.
6. Ask the model (Module 08 ``LLMService``) for a JSON interpretation.
7. Parse it strictly; validate every identifier, operator, value, aggregation
   and time expression against backend truth; resolve dates deterministically.
   Correctable problems get at most ``AI_UNDERSTAND_MAX_CORRECTIONS`` correction
   turns; executable content or restricted fields are never "corrected".
8. Decide clarification (one question, never repeated).
9. Authorize the intent's tables and columns with the permission engine.
10. Return the normalised intent and record an audit row and log event.

Nothing here generates or executes SQL or calls the company connector.
"""

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions.errors import ProviderError
from app.ai.services.llm_service import LLMService
from app.ai.understanding.normalizers.json_output import MalformedOutputError, extract_json_object
from app.ai.understanding.normalizers.text import normalize_question, tokens
from app.ai.understanding.normalizers.time_resolver import TimeResolver
from app.ai.understanding.prompts.understanding_prompt import (
    PROMPT_VERSION,
    build_messages,
    context_payload,
    correction_messages,
)
from app.ai.understanding.schemas.api import (
    ClarificationView,
    MatchedTermView,
    PermissionView as PermissionSummary,
    PreviewDiagnostics,
    PreviewResponse,
    SchemaSelectionView,
    TableScoreView,
    UnderstandingOptions,
    UnderstandingResponse,
    UnderstandingSource,
    UnderstandRequest,
    ValidationIssueView,
    ValidationView,
)
from app.ai.understanding.schemas.intent import (
    AggregationSpec,
    MAX_QUESTION_CHARS,
    AggregationType,
    BusinessIntent,
    IntentType,
    PermissionContext,
    TablePermission,
)
from app.ai.understanding.schemas.llm_output import LLMInterpretation
from app.ai.understanding.services.clarification import ClarificationEngine, ClarificationResult
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_context import SchemaContext
from app.ai.understanding.services.schema_relevance import (
    KeywordSchemaRelevanceSelector,
    RelevanceResult,
    SchemaRelevanceSelector,
    restricted_matches,
)
from app.ai.understanding.services.terminology_service import TermMatch, TerminologyService, stale_references, term_matches
from app.ai.understanding.services.timezone_service import BusinessTimezoneService
from app.ai.understanding.validators.intent_validator import InterpretationValidator, Issue, ValidatedInterpretation
from app.ai.understanding.validators.safety import question_problem
from app.authorization.engine import AuthorizationEngine, AuthorizationRequest
from app.authorization.policy import DATA_RESOURCE_TYPE
from app.core.config import Settings
from app.core.exceptions import ConflictError, InvalidInputError
from app.core.logging import get_request_id
from app.core.timezones import BUSINESS_TIMEZONE_SETTING_KEY
from app.database.transaction import transaction
from app.models.business_term import BusinessTerm, QuestionUnderstandingEvent
from app.models.user import User
from app.repositories.schema_registry_repository import SchemaSourceRepository

logger = logging.getLogger("app.ai.understanding")

DENIED_REASON = "The question needs data you are not permitted to query."
NO_ACCESS_REASON = "You have no data permissions for this schema. Ask an administrator for access."
# Interpretation is deterministic as far as the provider allows.
UNDERSTANDING_TEMPERATURE = 0.0
_AGGREGATING_INTENTS = {
    IntentType.METRIC_LOOKUP, IntentType.AGGREGATION, IntentType.COMPARISON,
    IntentType.TREND_ANALYSIS, IntentType.RANKING, IntentType.DISTRIBUTION,
}


class QuestionInvalidError(InvalidInputError):
    code = "QUESTION_INVALID"
    message = "The question cannot be interpreted."


class SchemaUnavailableError(ConflictError):
    code = "SCHEMA_NOT_AVAILABLE"
    message = "This schema is inactive or has no active tables, so questions about it cannot be interpreted."


@dataclass
class _Run:
    """Mutable state of one interpretation, for the response, the audit row and the log."""

    intent_id: uuid.UUID
    preview: bool
    started: float
    question: str
    original_question: str = ""
    schema: SchemaContext | None = None
    timezone: str = "UTC"
    timezone_source: str = "default"
    now: datetime | None = None
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    corrections: int = 0
    prompt_chars: int = 0
    terms_in_context: int = 0
    selection: RelevanceResult | None = None
    matches: list[TermMatch] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    error_category: str | None = None


class QuestionUnderstandingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        llm: LLMService,
        terminology: TerminologyService,
        engine: AuthorizationEngine,
        timezone: BusinessTimezoneService,
        selector: SchemaRelevanceSelector | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._settings = settings
        self._llm = llm
        self._terminology = terminology
        self._engine = engine
        self._timezone = timezone
        self._selector = selector or KeywordSchemaRelevanceSelector()
        self._clarifier = ClarificationEngine()
        self._clock = clock

    # =========================================================================================
    # options
    # =========================================================================================

    async def options(self) -> UnderstandingOptions:
        tz = await self._timezone.current()
        sources = await SchemaSourceRepository(self._session).active_sources()
        return UnderstandingOptions(
            timezone=tz.name,
            timezone_source=tz.source,
            timezone_setting_key=BUSINESS_TIMEZONE_SETTING_KEY,
            max_question_chars=MAX_QUESTION_CHARS,
            sources=[
                UnderstandingSource(id=s.id, name=s.name, database_type=s.database_type, schema_version=s.schema_version)
                for s in sources
            ],
        )

    # =========================================================================================
    # understanding
    # =========================================================================================

    async def understand(
        self, user: User, request: UnderstandRequest, *, preview: bool = False
    ) -> UnderstandingResponse | PreviewResponse:
        run = _Run(
            intent_id=uuid.uuid4(), preview=preview, started=time.perf_counter(),
            question=request.question, original_question=request.question,
        )
        try:
            response = await self._understand(user, request, run)
        except ProviderError as exc:
            run.error_category = exc.category.value
            await self._record(user, request, run, status="failed", validation="not_run")
            self._log(run, "failed", None, None)
            raise
        except QuestionInvalidError:
            run.error_category = "invalid_question"
            await self._record(user, request, run, status="rejected", validation="not_run")
            self._log(run, "rejected", None, None)
            raise
        await self._record(
            user, request, run, status=response.status, validation=response.validation.status,
            intent_type=response.intent.intent_type.value if response.intent else None,
            confidence=response.confidence, clarification=response.clarification.requires_clarification,
        )
        self._log(run, response.status, response.intent, response.validation.status)
        if preview:
            return PreviewResponse(**response.model_dump(), diagnostics=self._diagnostics(run))
        return response

    async def _understand(self, user: User, request: UnderstandRequest, run: _Run) -> UnderstandingResponse:
        # 1. the request
        question = normalize_question(request.question)
        run.question = question
        problem = question_problem(question)
        if problem is not None:
            raise QuestionInvalidError(problem)
        if not tokens(question):
            raise QuestionInvalidError("The question has no words to interpret.")

        # 2. schema and time
        schema = await self._terminology.schema_context(request.schema_source_id)
        run.schema = schema
        if not schema.is_active or not schema.tables:
            raise SchemaUnavailableError()
        tz = await self._timezone.current()
        run.timezone, run.timezone_source = tz.name, tz.source
        run.now = self._clock()
        resolver = TimeResolver(tz.name, run.now)

        # 3. permissions (backend engine, never the model)
        ai_context = await self._engine.ai_authorization_context(user)
        permissions = PermissionView(ai_context, schema)
        if not permissions.has_any_data_access:
            return self._denied(run, schema, permissions, NO_ACCESS_REASON, no_access=True)

        # 4. business terms
        usable, restricted_terms = self._split_terms(await self._terminology.active_terms(schema.source_id), schema, permissions)
        run.matches = term_matches(question, usable)
        matched_phrases = {" ".join(m.matched_text.lower().split()) for m in run.matches}
        restricted_hits = [m for m in term_matches(question, restricted_terms) if m.matched_text.lower() not in matched_phrases]
        if restricted_hits:
            return self._denied(run, schema, permissions, DENIED_REASON)

        # 5. relevant, permitted schema
        permitted = [key for key, table in schema.tables.items() if permissions.table_allowed(table.qualified)]
        term_tables = {t.lower() for m in run.matches for t in m.term.related_tables}
        run.selection = self._selector.select(
            question, schema, candidates=permitted, term_tables=term_tables,
            max_tables=self._settings.ai_understand_max_schema_tables,
        )
        context_terms = self._context_terms(usable, run.selection.tables, run.matches)
        run.terms_in_context = len(context_terms)
        payload = context_payload(
            question, schema, run.selection.tables, permissions, context_terms,
            today=resolver.today.isoformat(), timezone=tz.name,
        )
        messages = build_messages(payload)
        run.prompt_chars = sum(len(m["content"]) for m in messages)

        # 6-7. ask, parse, validate (bounded corrections)
        validator = InterpretationValidator(schema, permissions, usable, resolver)
        outcome = await self._interpret(user, run, question, messages, validator)
        if outcome is None:
            return self._rejected(run, schema, permissions)
        output, validated = outcome
        if validated.restricted:
            return self._denied(run, schema, permissions, DENIED_REASON)

        # A concept the model could not map that names restricted data is a denial, not a clarification.
        if validated.unmapped_concepts and self._names_restricted_data(question, schema, permissions):
            return self._denied(run, schema, permissions, DENIED_REASON)

        # 8. clarification
        clarification = self._clarifier.evaluate(output, validated, run.matches, answered=request.answered_clarifications)

        # 9. independent authorization of what the intent needs
        operation = self._operation(output, validated)
        if validated.referenced and not await self._authorized(user, permissions, validated.referenced, operation):
            return self._denied(run, schema, permissions, DENIED_REASON)

        # 10. the intent
        intent = self._intent(run, question, schema, permissions, output, validated, clarification)
        status = "needs_clarification" if clarification.requires_clarification else "understood"
        return UnderstandingResponse(
            request_id=get_request_id(),
            intent_id=run.intent_id,
            status=status,
            schema_source_id=schema.source_id,
            schema_version=schema.schema_version,
            confidence=intent.confidence,
            intent=intent,
            clarification=self._clarification_view(clarification),
            validation=ValidationView(
                status="corrected" if run.corrections else "passed",
                correction_attempts=run.corrections,
                issues=[ValidationIssueView(**i.as_dict()) for i in run.issues],
            ),
            permission=PermissionSummary(
                status="allowed",
                export_allowed=intent.permission_context.export_allowed,
                row_level_restrictions=intent.permission_context.row_level_restrictions,
            ),
            explanation=self._explanation(intent, clarification),
            provider=run.provider,
            model=run.model,
            latency_ms=_elapsed(run),
        )

    # --- the model ---------------------------------------------------------------------------------------

    async def _interpret(
        self,
        user: User,
        run: _Run,
        question: str,
        messages: list[dict[str, str]],
        validator: InterpretationValidator,
    ) -> tuple[LLMInterpretation, ValidatedInterpretation] | None:
        max_corrections = self._settings.ai_understand_max_corrections
        conversation = messages
        while True:
            response = await self._llm.generate(
                conversation,
                temperature=UNDERSTANDING_TEMPERATURE,
                max_tokens=self._settings.ai_understand_max_output_tokens,
                response_format="json_object",
                operation="question_understanding",
                metadata={"feature": "question_understanding", "preview": run.preview, "attempt": run.attempts + 1},
                user_id=user.id,
            )
            run.attempts += 1
            run.provider, run.model = response.provider, response.model
            problems, correctable, parsed = self._parse(response.content)
            validated: ValidatedInterpretation | None = None
            if parsed is not None:
                validated = validator.validate(question, parsed)
                if not validated.issues:
                    return parsed, validated
                problems = [i for i in validated.issues]
                correctable = not validated.blocking_issues
                if not correctable:
                    run.issues = problems
                    return parsed, validated  # restricted: handled by the caller as a denial
            run.issues = problems
            if not correctable or run.corrections >= max_corrections:
                return None
            run.corrections += 1
            logger.info(
                "Requesting a corrected interpretation",
                extra={"event": "ai.understanding.correction", "intent_id": str(run.intent_id),
                       "issue_codes": sorted({p.code for p in problems})},
            )
            conversation = correction_messages(messages, response.content, [p.message for p in problems])

    @staticmethod
    def _parse(content: str) -> tuple[list[Issue], bool, LLMInterpretation | None]:
        """(issues, correctable, parsed). Never echoes the reply's text into an issue."""
        try:
            text = extract_json_object(content)
        except MalformedOutputError as exc:
            return [Issue("malformed_json", str(exc))], True, None
        try:
            return [], True, LLMInterpretation.model_validate_json(text)
        except ValidationError as exc:
            issues: list[Issue] = []
            unsafe = False
            for error in exc.errors(include_input=False, include_url=False)[:15]:
                message = str(error.get("msg", "invalid value"))
                location = tuple(str(p) for p in error.get("loc", ()))
                if "SQL fragment" in message or "code or tool" in message or "control characters" in message:
                    unsafe = True
                    issues.append(Issue("executable_content", "The reply contained SQL, code or instructions.", location, False))
                elif error.get("type") == "json_invalid":
                    issues.append(Issue("malformed_json", "The reply was not valid JSON.", location))
                elif error.get("type") == "extra_forbidden":
                    issues.append(Issue("unknown_field", f"Field '{'.'.join(location)}' is not part of the contract.", location))
                elif error.get("type") in ("enum", "literal_error"):
                    issues.append(Issue("unsupported_value", f"{'.'.join(location)}: {message}", location))
                else:
                    issues.append(Issue("invalid_structure", f"{'.'.join(location) or 'reply'}: {message}", location))
            return issues, not unsafe, None

    # --- terms -------------------------------------------------------------------------------------------

    @staticmethod
    def _split_terms(
        terms: Sequence[BusinessTerm], schema: SchemaContext, permissions: PermissionView
    ) -> tuple[list[BusinessTerm], list[BusinessTerm]]:
        """Terms the user may use, and terms that need data they may not query."""
        usable: list[BusinessTerm] = []
        restricted: list[BusinessTerm] = []
        for term in terms:
            stale = set(stale_references(term, schema))
            tables = [t for t in term.related_tables if t not in stale]
            columns = [c for c in term.related_columns if c not in stale]
            if not tables and not columns:
                continue  # nothing registered is left: the term cannot be used
            blocked = any(not permissions.table_allowed(t) for t in tables) or any(
                not permissions.column_allowed(c.rsplit(".", 1)[0], c.rsplit(".", 1)[1]) for c in columns
            )
            (restricted if blocked else usable).append(term)
        return usable, restricted

    @staticmethod
    def _context_terms(usable: Sequence[BusinessTerm], tables: Sequence[str], matches: Sequence[TermMatch]) -> list[BusinessTerm]:
        """Matched terms first, then terms about the selected tables (at most 40)."""
        selected = {t.lower() for t in tables}
        chosen = [m.term for m in matches]
        for term in usable:
            if term not in chosen and any(t.lower() in selected for t in term.related_tables):
                chosen.append(term)
        return chosen[:40]

    # --- permissions -------------------------------------------------------------------------------------

    @staticmethod
    def _names_restricted_data(question: str, schema: SchemaContext, permissions: PermissionView) -> bool:
        restricted_tables = [key for key, t in schema.tables.items() if not permissions.table_allowed(t.qualified)]
        if restricted_matches(question, schema, restricted_tables):
            return True
        question_tokens = tokens(question)
        for table in schema.tables.values():
            if not permissions.table_allowed(table.qualified):
                continue
            for column in table.columns.values():
                if not permissions.column_allowed(table.qualified, column.name) and tokens(column.name) & question_tokens:
                    return True
        return False

    @staticmethod
    def _operation(output: LLMInterpretation, validated: ValidatedInterpretation) -> str:
        aggregating = any(m.aggregation != AggregationType.NONE for m in validated.metrics)
        return "aggregate" if aggregating or (output.intent_type in _AGGREGATING_INTENTS and validated.metrics) else "read"

    async def _authorized(self, user: User, permissions: PermissionView, referenced: dict[str, set[str]], operation: str) -> bool:
        """Ask the permission engine for exactly these tables, columns and operation (any data action may allow it)."""
        for action in permissions.actions():
            tables: list[str] = []
            columns: list[str] = []
            for table, names in referenced.items():
                policy_table = permissions.policy_table(table, action)
                if policy_table is None:
                    break
                tables.append(policy_table)
                columns.extend(permissions.policy_column(table, action, name) for name in sorted(names))
            else:
                decision = await self._engine.authorize(
                    AuthorizationRequest(
                        user_id=user.id, resource_type=DATA_RESOURCE_TYPE, action=action,
                        tables=tuple(tables), columns=tuple(columns), operation=operation,
                    )
                )
                if decision.allowed:
                    return True
        return False

    # --- building responses ---------------------------------------------------------------------------------

    def _intent(
        self,
        run: _Run,
        question: str,
        schema: SchemaContext,
        permissions: PermissionView,
        output: LLMInterpretation,
        validated: ValidatedInterpretation,
        clarification: ClarificationResult,
    ) -> BusinessIntent:
        referenced_tables = sorted(validated.referenced, key=str.lower)
        term_tables = [
            schema.table(t).qualified  # type: ignore[union-attr]
            for m in run.matches
            for t in m.term.related_tables
            if schema.table(t) is not None and permissions.table_allowed(t)
        ]
        candidate_tables = list(dict.fromkeys([*referenced_tables, *term_tables]))
        candidate_columns = sorted(
            {f"{table}.{column}" for table, columns in validated.referenced.items() for column in columns}, key=str.lower
        )
        table_permissions = [
            TablePermission(
                table=table,
                operations=permissions.operations(table),
                row_filter=permissions.row_filter(table),
                allow_export=permissions.export_allowed(table),
            )
            for table in candidate_tables
        ]
        confidence = output.confidence
        if clarification.requires_clarification:
            confidence = min(confidence, 0.5)
        if run.corrections:
            confidence = min(confidence, 0.8)
        metric_aggregations = {m.aggregation for m in validated.metrics}
        grouping = [d.name for d in validated.dimensions]
        ambiguities = list(dict.fromkeys([*output.ambiguities, *clarification.deferred]))
        return BusinessIntent(
            intent_id=run.intent_id,
            original_question=run.original_question,
            normalized_question=output.normalized_question,
            intent_type=output.intent_type,
            confidence=round(confidence, 3),
            requires_clarification=clarification.requires_clarification,
            schema_source_id=schema.source_id,
            schema_version=schema.schema_version,
            requested_metrics=validated.metrics,
            requested_dimensions=validated.dimensions,
            requested_attributes=validated.attributes,
            candidate_tables=candidate_tables,
            candidate_columns=candidate_columns,
            filters=validated.filters,
            time=validated.time,
            aggregation=AggregationSpec(
                aggregation_type=next(iter(metric_aggregations)) if len(metric_aggregations) == 1 else None,
                grouping_dimensions=grouping,
                sorting=validated.sorting,
                limit=output.limit,
            ),
            business_context=output.business_context,
            assumptions=list(dict.fromkeys([*output.assumptions, *validated.assumptions])),
            ambiguities=ambiguities,
            clarification_question=clarification.clarification_question,
            permission_context=PermissionContext(
                status="allowed",
                policy_revision=permissions.policy_revision,
                tables=table_permissions,
                export_allowed=bool(table_permissions) and all(t.allow_export for t in table_permissions),
                row_level_restrictions=any(t.row_filter is not None for t in table_permissions),
            ),
        )

    def _denied(
        self, run: _Run, schema: SchemaContext, permissions: PermissionView, reason: str, *, no_access: bool = False
    ) -> UnderstandingResponse:
        return UnderstandingResponse(
            request_id=get_request_id(),
            intent_id=run.intent_id,
            status="access_denied",
            schema_source_id=schema.source_id,
            schema_version=schema.schema_version,
            intent=None,
            clarification=ClarificationView(requires_clarification=False),
            validation=ValidationView(
                status="not_run" if run.attempts == 0 else ("corrected" if run.corrections else "passed"),
                correction_attempts=run.corrections,
            ),
            permission=PermissionSummary(status="no_data_access" if no_access else "denied", reason=reason),
            explanation=reason,
            provider=run.provider,
            model=run.model,
            latency_ms=_elapsed(run),
        )

    def _rejected(self, run: _Run, schema: SchemaContext, permissions: PermissionView) -> UnderstandingResponse:
        unsafe = any(i.code == "executable_content" for i in run.issues)
        explanation = (
            "The interpretation contained content that is not allowed, so it was discarded. Rephrase the question in plain language."
            if unsafe
            else "The question could not be interpreted reliably against the registered schema. Try naming the measure, "
            "the period and any grouping explicitly."
        )
        return UnderstandingResponse(
            request_id=get_request_id(),
            intent_id=run.intent_id,
            status="rejected",
            schema_source_id=schema.source_id,
            schema_version=schema.schema_version,
            intent=None,
            clarification=ClarificationView(requires_clarification=False),
            validation=ValidationView(
                status="rejected",
                correction_attempts=run.corrections,
                issues=[ValidationIssueView(**i.as_dict()) for i in run.issues],
            ),
            permission=PermissionSummary(status="allowed"),
            explanation=explanation,
            provider=run.provider,
            model=run.model,
            latency_ms=_elapsed(run),
        )

    @staticmethod
    def _clarification_view(result: ClarificationResult) -> ClarificationView:
        return ClarificationView(
            requires_clarification=result.requires_clarification,
            reason=result.reason,
            missing_information=list(result.missing_information),
            clarification_question=result.clarification_question,
            key=result.key,
        )

    @staticmethod
    def _explanation(intent: BusinessIntent, clarification: ClarificationResult) -> str:
        if clarification.requires_clarification:
            if clarification.clarification_question:
                return "More information is needed before this question can be answered."
            return "The question is still missing information after a clarification. Rephrase it with the missing details."
        parts = [f"Understood as {intent.intent_type.value.replace('_', ' ')}"]
        if intent.requested_metrics:
            parts.append("of " + ", ".join(m.name for m in intent.requested_metrics))
        if intent.requested_dimensions:
            parts.append("by " + ", ".join(d.name for d in intent.requested_dimensions))
        if intent.time.time_range:
            parts.append(f"for {intent.time.time_range.label.lower()}")
        if intent.time.comparison_period:
            parts.append(f"compared with {intent.time.comparison_period.label.lower()}")
        return " ".join(parts) + "."

    def _diagnostics(self, run: _Run) -> PreviewDiagnostics:
        selection = run.selection
        return PreviewDiagnostics(
            timezone=run.timezone,
            timezone_source=run.timezone_source,
            reference_time=(run.now or self._clock()).isoformat(),
            prompt_version=PROMPT_VERSION,
            schema_selection=SchemaSelectionView(
                strategy=selection.strategy,
                selected_tables=list(selection.tables),
                scores=[TableScoreView(table=s.table, score=s.score, reasons=list(s.reasons)) for s in selection.scores],
                truncated=selection.truncated,
            )
            if selection
            else None,
            matched_terms=[MatchedTermView(id=m.term.id, name=m.term.name, matched_text=m.matched_text) for m in run.matches],
            terms_in_context=run.terms_in_context,
            prompt_chars=run.prompt_chars,
            llm_attempts=run.attempts,
        )

    # --- audit and logs ----------------------------------------------------------------------------------

    async def _record(
        self,
        user: User,
        request: UnderstandRequest,
        run: _Run,
        *,
        status: str,
        validation: str,
        intent_type: str | None = None,
        confidence: float | None = None,
        clarification: bool = False,
    ) -> None:
        """One audit row per request. A storage failure is logged, never raised."""
        try:
            async with transaction(self._session):
                self._session.add(
                    QuestionUnderstandingEvent(
                        request_id=get_request_id(),
                        intent_id=run.intent_id,
                        user_id=user.id,
                        schema_source_id=run.schema.source_id if run.schema else None,
                        schema_version=run.schema.schema_version if run.schema else None,
                        is_preview=run.preview,
                        provider=run.provider,
                        model=run.model,
                        latency_ms=_elapsed(run),
                        llm_attempts=run.attempts,
                        question_chars=len(request.question),
                        intent_type=intent_type,
                        confidence=confidence,
                        status=status,
                        requires_clarification=clarification,
                        validation_outcome=validation,
                        error_category=run.error_category,
                        details={"issue_codes": sorted({i.code for i in run.issues}), "corrections": run.corrections},
                    )
                )
        except SQLAlchemyError as exc:
            logger.error(
                "Question understanding audit could not be stored",
                extra={"event": "ai.understanding.audit_failed", "error_type": type(exc).__name__},
            )

    def _log(self, run: _Run, status: str, intent: BusinessIntent | None, validation: str | None) -> None:
        logger.info(
            "Question interpreted",
            extra={
                "event": "ai.understanding.completed",
                "intent_id": str(run.intent_id),
                "schema_source_id": str(run.schema.source_id) if run.schema else None,
                "schema_version": run.schema.schema_version if run.schema else None,
                "provider": run.provider,
                "model": run.model,
                "latency_ms": _elapsed(run),
                "llm_attempts": run.attempts,
                "status": status,
                "intent_type": intent.intent_type.value if intent else None,
                "confidence": intent.confidence if intent else None,
                "requires_clarification": intent.requires_clarification if intent else False,
                "validation_outcome": validation,
                "error_category": run.error_category,
                "issue_codes": sorted({i.code for i in run.issues}),
                "preview": run.preview,
                "question_chars": len(run.question),
            },
        )


def _elapsed(run: _Run) -> int:
    return max(0, round((time.perf_counter() - run.started) * 1000))


__all__ = ["QuestionInvalidError", "QuestionUnderstandingService", "SchemaUnavailableError", "Any"]
