"""Deterministic clarification detection.

Decides whether an interpretation has enough information to proceed and, if
not, asks **one** focused question. Rules, in priority order:

1. A phrase in the question matches several active business terms
   ("sales" -> Gross sales / Net sales): ask which one. Never pick one silently.
2. A business concept has no registered definition and maps to no column
   ("profit" with no "Profit" term): say it is not defined and ask what it
   should be measured by. Never invent a definition.
3. A comparison without a period to compare: ask which periods.
4. A ranking without a measure: ask what to rank by.
5. Nothing to measure or show at all, or the intent is unknown: ask what the
   user wants to see.
6. The model asked for clarification: its question is used only if none of the
   above applies, and it is dropped when the backend already has the answer
   (for example it asks about a term that has exactly one registered definition).

Each clarification has a stable ``key``. A request may list the keys it already
answered (``answered_clarifications``); a key is never asked twice. When every
remaining clarification was already asked, the result says it is still
unresolved instead of repeating the question.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.ai.understanding.schemas.intent import ClarificationReason, IntentType, MissingInformation
from app.ai.understanding.schemas.llm_output import LLMInterpretation
from app.ai.understanding.services.terminology_service import TermMatch, ambiguous_phrases
from app.ai.understanding.validators.intent_validator import ValidatedInterpretation
from app.models.business_term import BusinessTerm


@dataclass(frozen=True, slots=True)
class Clarification:
    key: str
    reason: ClarificationReason
    missing_information: tuple[MissingInformation, ...]
    question: str


@dataclass(frozen=True, slots=True)
class ClarificationResult:
    requires_clarification: bool
    reason: ClarificationReason | None = None
    missing_information: tuple[MissingInformation, ...] = ()
    clarification_question: str | None = None
    key: str | None = None
    # Other open points, reported as ambiguities but not asked now (one question at a time).
    deferred: tuple[str, ...] = field(default_factory=tuple)


NOT_REQUIRED = ClarificationResult(requires_clarification=False)


def _key(kind: str, subject: str = "") -> str:
    subject = "".join(ch if ch.isalnum() or ch in " .-_" else " " for ch in subject.lower()).strip()[:100]
    return f"{kind}:{subject}" if subject else kind


def _names(terms: Sequence[BusinessTerm]) -> str:
    names = sorted({t.name for t in terms}, key=str.lower)
    return ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else names[0]


class ClarificationEngine:
    def evaluate(
        self,
        output: LLMInterpretation,
        validated: ValidatedInterpretation,
        matches: Sequence[TermMatch],
        *,
        answered: Sequence[str] = (),
    ) -> ClarificationResult:
        candidates = self._candidates(output, validated, matches)
        if not candidates:
            return NOT_REQUIRED
        asked = {a.lower() for a in answered}
        open_items = [c for c in candidates if c.key.lower() not in asked]
        if not open_items:
            first = candidates[0]
            return ClarificationResult(
                requires_clarification=True,
                reason=ClarificationReason.UNRESOLVED_AFTER_CLARIFICATION,
                missing_information=first.missing_information,
                clarification_question=None,
                key=first.key,
            )
        first, rest = open_items[0], open_items[1:]
        return ClarificationResult(
            requires_clarification=True,
            reason=first.reason,
            missing_information=first.missing_information,
            clarification_question=first.question,
            key=first.key,
            deferred=tuple(c.question for c in rest),
        )

    def _candidates(
        self, output: LLMInterpretation, validated: ValidatedInterpretation, matches: Sequence[TermMatch]
    ) -> list[Clarification]:
        items: list[Clarification] = []
        used_ids = {t.id for t in validated.terms_used}

        # 1. Several registered definitions for one phrase.
        for phrase, terms in sorted(ambiguous_phrases(matches).items()):
            chosen = [t for t in terms if t.id in used_ids]
            if len(chosen) == 1 and any(phrase == t.name.lower() for t in chosen):
                continue  # the user named the term exactly
            items.append(
                Clarification(
                    _key("ambiguous_term", phrase),
                    ClarificationReason.AMBIGUOUS_TERM,
                    (MissingInformation.TERM_MEANING,),
                    f"'{phrase}' can mean {_names(terms)}. Which one do you mean?",
                )
            )

        # 2. Concepts with no registered definition and no column.
        for concept in validated.unmapped_concepts:
            items.append(
                Clarification(
                    _key("missing_definition", concept),
                    ClarificationReason.MISSING_DEFINITION,
                    (MissingInformation.METRIC_DEFINITION,),
                    f"'{concept}' has no registered business definition. Which registered measure should be used "
                    "for it? (An administrator can define it under Business terms.)",
                )
            )

        intent = output.intent_type
        # 3. Comparison without a period.
        if intent == IntentType.COMPARISON and validated.time.time_range is None and not validated.dimensions:
            items.append(
                Clarification(
                    _key("comparison_period"),
                    ClarificationReason.MISSING_COMPARISON_PERIOD,
                    (MissingInformation.COMPARISON_PERIOD,),
                    "Which periods should be compared, for example this month and last month?",
                )
            )
        elif intent == IntentType.COMPARISON and validated.time.time_range is not None and validated.time.comparison_period is None and not validated.dimensions:
            items.append(
                Clarification(
                    _key("comparison_period"),
                    ClarificationReason.MISSING_COMPARISON_PERIOD,
                    (MissingInformation.COMPARISON_PERIOD,),
                    f"What should {validated.time.time_range.label.lower()} be compared with: the previous period or the same period last year?",
                )
            )

        # 4. Ranking without a measure.
        if intent == IntentType.RANKING and not validated.metrics and not validated.unmapped_concepts:
            items.append(
                Clarification(
                    _key("ranking_metric"),
                    ClarificationReason.MISSING_RANKING_METRIC,
                    (MissingInformation.RANKING_METRIC,),
                    "What should the ranking be based on, for example revenue or number of orders?",
                )
            )

        # 5. Nothing to show.
        nothing = not (validated.metrics or validated.dimensions or validated.attributes or validated.filters)
        if (intent == IntentType.UNKNOWN or nothing) and not items:
            items.append(
                Clarification(
                    _key("unclear_metric"),
                    ClarificationReason.UNCLEAR_METRIC,
                    (MissingInformation.METRIC,),
                    "What would you like to see? Name a measure (for example revenue or order count) or the records you need.",
                )
            )

        # 6. The model's own request, unless already covered or answerable by the backend.
        clarification = output.clarification
        if clarification is not None and clarification.needed and clarification.question and not items:
            if not self._answerable(clarification.missing_information, validated, matches):
                items.append(
                    Clarification(
                        _key("model", clarification.question),
                        ClarificationReason.MODEL_REQUESTED,
                        tuple(clarification.missing_information) or (MissingInformation.OTHER,),
                        clarification.question,
                    )
                )
        return items

    @staticmethod
    def _answerable(
        missing: Sequence[MissingInformation], validated: ValidatedInterpretation, matches: Sequence[TermMatch]
    ) -> bool:
        """True when the backend already has what the model asks for."""
        if not missing:
            return False
        known = set()
        if validated.metrics and all(m.term_id or m.column for m in validated.metrics):
            known |= {MissingInformation.METRIC, MissingInformation.METRIC_DEFINITION, MissingInformation.RANKING_METRIC}
        if matches and not ambiguous_phrases(matches):
            known.add(MissingInformation.TERM_MEANING)
        if validated.time.time_range is not None:
            known.add(MissingInformation.TIME_RANGE)
        if validated.time.comparison_period is not None:
            known.add(MissingInformation.COMPARISON_PERIOD)
        return all(m in known for m in missing)
