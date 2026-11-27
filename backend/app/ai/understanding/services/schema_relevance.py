"""Deterministic schema-relevance selection.

Large schemas are not sent to the model whole. ``KeywordSchemaRelevanceSelector``
scores every registered table against the question using only words: table
and column names, descriptions, business purposes and meanings, and the
tables that matched business terms point at. The best tables are kept, plus
tables directly related to them through registered relationships (so join
paths survive), up to a limit. No embeddings, vector search or ranking model.

``SchemaRelevanceSelector`` is the replacement point: a later module can plug in
a smarter selector with the same signature.

Scores per table (summed):

* table name word in the question: 6
* a table that a matched business term relates to: 8
* column name word in the question: 3 per column (at most 4 columns)
* word of the table's description or business purpose: 1 each (at most 3)
* word of a column's description or business meaning: 1 each (at most 3)

A schema with at most ``max_tables`` tables is sent whole (it is small).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ai.understanding.normalizers.text import tokens
from app.ai.understanding.services.schema_context import SchemaContext, TableInfo


@dataclass(frozen=True, slots=True)
class TableScore:
    table: str  # qualified
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    tables: tuple[str, ...]  # qualified, in selection order
    scores: tuple[TableScore, ...]  # every table with a positive score, best first
    strategy: str  # "all_tables" (small schema) or "keyword"
    truncated: bool  # some matching tables did not fit


class SchemaRelevanceSelector(Protocol):
    def select(
        self,
        question: str,
        schema: SchemaContext,
        *,
        candidates: Collection[str],
        term_tables: Collection[str] = (),
        max_tables: int,
    ) -> RelevanceResult: ...


def score_table(question_tokens: set[str], table: TableInfo, term_tables: Collection[str]) -> TableScore:
    score = 0
    reasons: list[str] = []
    if tokens(table.table_name) & question_tokens:
        score += 6
        reasons.append("table name")
    if table.qualified.lower() in term_tables:
        score += 8
        reasons.append("business term")
    column_hits = [c.name for c in table.columns.values() if tokens(c.name) & question_tokens][:4]
    if column_hits:
        score += 3 * len(column_hits)
        reasons.append("column names: " + ", ".join(column_hits))
    described = len((tokens(table.description) | tokens(table.business_purpose)) & question_tokens)
    if described:
        score += min(3, described)
        reasons.append("table description")
    meaning_words: set[str] = set()
    for column in table.columns.values():
        meaning_words |= tokens(column.description) | tokens(column.business_meaning)
    meant = len(meaning_words & question_tokens)
    if meant:
        score += min(3, meant)
        reasons.append("column descriptions")
    return TableScore(table.qualified, score, tuple(reasons))


class KeywordSchemaRelevanceSelector:
    def select(
        self,
        question: str,
        schema: SchemaContext,
        *,
        candidates: Collection[str],
        term_tables: Collection[str] = (),
        max_tables: int,
    ) -> RelevanceResult:
        """Choose among ``candidates`` (lower-case qualified names the user may see)."""
        wanted = {c.lower() for c in candidates}
        term_keys = {t.lower() for t in term_tables}
        question_tokens = tokens(question)
        scored = sorted(
            (score_table(question_tokens, schema.tables[key], term_keys) for key in wanted if key in schema.tables),
            key=lambda s: (-s.score, s.table.lower()),
        )
        positive = [s for s in scored if s.score > 0]
        if len(wanted) <= max_tables:
            ordered = [s.table for s in positive] + [s.table for s in scored if s.score == 0]
            return RelevanceResult(tuple(ordered), tuple(positive), "all_tables", False)

        chosen: list[str] = []
        for item in positive:
            if len(chosen) >= max_tables:
                break
            chosen.append(item.table)
        # Keep join paths: add tables directly related to the chosen ones while there is room.
        for related in sorted(schema.related_tables(chosen)):
            if len(chosen) >= max_tables:
                break
            if related in wanted and schema.tables[related].qualified not in chosen:
                chosen.append(schema.tables[related].qualified)
        truncated = len(positive) > len([c for c in chosen if any(p.table == c for p in positive)])
        return RelevanceResult(tuple(chosen), tuple(positive), "keyword", truncated)


def restricted_matches(question: str, schema: SchemaContext, restricted: Sequence[str]) -> list[str]:
    """Restricted tables the question clearly names (table name word match). Used only to say "restricted"."""
    question_tokens = tokens(question)
    return [
        schema.tables[key].qualified
        for key in restricted
        if key in schema.tables and tokens(schema.tables[key].table_name) & question_tokens
    ]
