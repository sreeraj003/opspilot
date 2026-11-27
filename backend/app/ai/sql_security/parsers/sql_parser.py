"""Parsing the exact SQL with SQLGlot, the guard's authoritative parser.

The text is tokenized and parsed in the schema's dialect. Regular expressions
are never used to understand the SQL. Rejected before any AST inspection:

* empty, oversized or control-character SQL;
* comments of any kind (comment-based obfuscation, MySQL ``/*! */`` hints);
* stacked statements (any semicolon that is not the single last token);
* tokens or quoting from another dialect (``LIMIT`` in T-SQL, ``TOP`` in
  PostgreSQL or MySQL, backticks in PostgreSQL, double-quoted identifiers in
  MySQL), using Module 10's dialect registry;
* parser errors (SQLGlot is never asked to recover or reinterpret);
* anything but exactly one statement whose root is a SELECT or a set
  operation of SELECTs. Commands SQLGlot could not model (``exp.Command``)
  are rejected, never passed through.

SQL is never executed here.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ParseError, SqlglotError, TokenError

from app.ai.sql_generation.dialects.registry import SQLDialect
from app.ai.sql_security.validators.violations import Category, GuardRejected, Violation, violation


@dataclass(frozen=True, slots=True)
class ParsedSQL:
    text: str  # stripped, without a trailing semicolon
    statement: exp.Expression  # root: Select or SetOperation


def _invalid(dialect: SQLDialect, internal: str) -> Violation:
    return violation(
        "invalid_syntax", Category.PARSE_ERROR, f"The query is not valid {dialect.display_name} SQL.", internal
    )


def parse_exact(sql: str, dialect: SQLDialect, *, max_length: int) -> ParsedSQL:
    text = sql.strip()
    if not text:
        raise GuardRejected([violation("empty_sql", Category.PARSE_ERROR, "The query is empty.")])
    if len(text) > max_length:
        raise GuardRejected(
            [violation("sql_too_long", Category.COMPLEXITY_LIMIT, f"The query is longer than {max_length} characters.",
                       f"SQL length {len(text)} > {max_length}")]
        )
    if any((ord(ch) < 32 and ch not in "\t\r\n") or ord(ch) == 127 for ch in text):
        raise GuardRejected(
            [violation("control_characters", Category.UNSAFE_CONSTRUCT, "The query contains control characters.")]
        )
    tokens = _tokens(text, dialect)
    statement = _parse(text, dialect, tokens)
    return ParsedSQL(text=text.rstrip().rstrip(";").rstrip(), statement=statement)


def _tokens(text: str, dialect: SQLDialect) -> list:  # type: ignore[type-arg]
    try:
        tokens = Dialect.get_or_raise(dialect.sqlglot_dialect).tokenize(text)
    except TokenError as exc:
        raise GuardRejected([_invalid(dialect, f"tokenizer: {type(exc).__name__}")]) from None
    problems: list[Violation] = []
    if any(t.comments for t in tokens):
        problems.append(
            violation("comment_not_allowed", Category.UNSAFE_CONSTRUCT, "Comments are not allowed in the query.")
        )
    semicolons = [i for i, t in enumerate(tokens) if t.token_type.name == "SEMICOLON"]
    if semicolons and (len(semicolons) > 1 or semicolons[0] != len(tokens) - 1):
        raise GuardRejected(
            [violation("multiple_statements", Category.MULTIPLE_STATEMENTS, "Only one SQL statement is allowed.",
                       f"{len(semicolons)} statement separator(s)")]
        )
    for index, token in enumerate(tokens):
        name = token.token_type.name
        quote = text[token.start : token.start + 1]
        if name == "UNKNOWN":
            problems.append(_dialect_problem(dialect, f"unknown token {token.text!r}"))
        elif name in dialect.forbidden_tokens:
            problems.append(_dialect_problem(dialect, f"{token.text.upper()} is not {dialect.display_name} syntax"))
        elif name in ("STRING", "IDENTIFIER") and quote in dialect.forbidden_quote_chars:
            problems.append(_dialect_problem(dialect, f"{quote} quoting is not {dialect.display_name} syntax"))
        elif name == "L_BRACKET" and dialect.bracket_rule != "identifier":
            after_array = index > 0 and tokens[index - 1].token_type.name == "ARRAY"
            if dialect.bracket_rule == "forbidden" or not after_array:
                problems.append(_dialect_problem(dialect, "square brackets"))
    if problems:
        raise GuardRejected(problems)
    return tokens


def _dialect_problem(dialect: SQLDialect, internal: str) -> Violation:
    return violation(
        "dialect_mismatch", Category.DIALECT_MISMATCH,
        f"The query uses syntax that is not {dialect.display_name} SQL.", internal,
    )


def _parse(text: str, dialect: SQLDialect, tokens: list) -> exp.Expression:  # type: ignore[type-arg]
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect.sqlglot_dialect) if s is not None]
    except (ParseError, TokenError) as exc:
        raise GuardRejected([_invalid(dialect, f"parser: {type(exc).__name__}")]) from None
    except SqlglotError as exc:  # anything else the parser refuses
        raise GuardRejected([_invalid(dialect, f"parser: {type(exc).__name__}")]) from None
    if not statements:
        raise GuardRejected([violation("empty_sql", Category.PARSE_ERROR, "The query is empty.")])
    if len(statements) > 1:
        raise GuardRejected(
            [violation("multiple_statements", Category.MULTIPLE_STATEMENTS, "Only one SQL statement is allowed.")]
        )
    statement = statements[0]
    root = statement
    while isinstance(root, exp.Subquery) and not root.alias:
        root = root.this
    if isinstance(root, exp.Command):
        keyword = str(root.this or "command").split()[0].upper()
        raise GuardRejected(
            [violation("statement_not_allowed", Category.STATEMENT_NOT_ALLOWED,
                       "Only a single read-only SELECT statement is allowed.", f"unparsed command {keyword}")]
        )
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        raise GuardRejected(
            [violation("statement_not_allowed", Category.STATEMENT_NOT_ALLOWED,
                       "Only a single read-only SELECT statement is allowed.", f"root statement {type(root).__name__}")]
        )
    return root
