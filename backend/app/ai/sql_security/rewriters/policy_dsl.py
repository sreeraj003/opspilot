"""Module 04's structured row-policy DSL -> SQLGlot AST expressions.

The DSL is data, never code. Two spellings are accepted:

* the normalised form the permission engine stores and returns
  (``{"column": "orders.region", "operator": "equals", "value": "South",
  "value_type": "string"}`` and ``{"logic": "and" | "or" | "not",
  "conditions": [...]}``);
* the field form (``{"field": "region", "operator": "equals", "value": ...}``
  and ``{"operator": "and" | "or" | "not", "conditions": [...]}``).

Every condition must name a registered column of the table being protected;
values must have the type the operator needs. Anything unexpected raises
``PolicyTranslationError`` and the query is rejected: a restriction is never
dropped, loosened or guessed.

Values become literals in the AST (never concatenated text), so quoting is the
generator's job. Dates and timestamps are ``CAST('...' AS DATE/TIMESTAMP)``;
booleans are ``TRUE`` / ``FALSE`` (``1`` / ``0`` in T-SQL).
"""

from datetime import date, datetime
from typing import Any

from sqlglot import exp

from app.ai.understanding.services.schema_context import TableInfo

MAX_DEPTH = 8
_COMPARISONS: dict[str, type[exp.Expression]] = {
    "equals": exp.EQ,
    "not_equals": exp.NEQ,
    "greater_than": exp.GT,
    "greater_than_or_equal": exp.GTE,
    "less_than": exp.LT,
    "less_than_or_equal": exp.LTE,
}
_LOGIC = ("and", "or", "not")


class PolicyTranslationError(ValueError):
    """The restriction cannot be expressed safely for this table."""


def policy_to_expression(node: Any, table: TableInfo, alias: exp.Identifier, dialect: str, depth: int = 0) -> exp.Expression:
    if depth > MAX_DEPTH:
        raise PolicyTranslationError("restriction nested too deeply")
    if not isinstance(node, dict):
        raise PolicyTranslationError("restriction node is not an object")
    logic = node.get("logic")
    if logic is None and node.get("operator") in _LOGIC and "conditions" in node:
        logic = node["operator"]
    if logic is not None:
        conditions = node.get("conditions")
        if logic not in _LOGIC or not isinstance(conditions, list) or not conditions:
            raise PolicyTranslationError("invalid logical group")
        parts = [policy_to_expression(c, table, alias, dialect, depth + 1) for c in conditions]
        if logic == "not":
            inner = parts[0] if len(parts) == 1 else exp.and_(*[exp.paren(p, copy=False) for p in parts], copy=False)
            return exp.Not(this=exp.paren(inner, copy=False))
        joined = exp.and_ if logic == "and" else exp.or_
        return exp.paren(joined(*[exp.paren(p, copy=False) for p in parts], copy=False), copy=False)
    return _condition(node, table, alias, dialect)


def _column(node: dict[str, Any], table: TableInfo, alias: exp.Identifier) -> exp.Column:
    reference = node.get("column", node.get("field"))
    if not isinstance(reference, str) or not reference:
        raise PolicyTranslationError("condition without a column")
    prefix, _, name = reference.rpartition(".")
    if prefix and prefix.lower() not in (table.table_name.lower(), table.qualified.lower()):
        raise PolicyTranslationError("condition refers to another table")
    info = table.column(name)
    if info is None:
        raise PolicyTranslationError("condition column is not registered")
    return exp.Column(this=exp.to_identifier(info.name), table=alias.copy())


def _condition(node: dict[str, Any], table: TableInfo, alias: exp.Identifier, dialect: str) -> exp.Expression:
    operator = node.get("operator")
    column = _column(node, table, alias)
    value_type = node.get("value_type")
    if operator == "is_null":
        return exp.Is(this=column, expression=exp.Null())
    if operator == "is_not_null":
        return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
    if "value" not in node:
        raise PolicyTranslationError("condition without a value")
    value = node["value"]
    if operator in _COMPARISONS:
        return _COMPARISONS[operator](this=column, expression=_literal(value, value_type, dialect))
    if operator in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise PolicyTranslationError("list operator needs a non-empty list")
        expression = exp.In(this=column, expressions=[_literal(v, value_type, dialect) for v in value])
        return expression if operator == "in" else exp.Not(this=exp.paren(expression, copy=False))
    if operator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise PolicyTranslationError("between needs two values")
        return exp.Between(this=column, low=_literal(value[0], value_type, dialect), high=_literal(value[1], value_type, dialect))
    raise PolicyTranslationError("unsupported operator")


def _literal(value: Any, value_type: Any, dialect: str) -> exp.Expression:
    kind = value_type or _infer(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise PolicyTranslationError("boolean value expected")
        if dialect == "tsql":
            return exp.Literal.number(1 if value else 0)
        return exp.Boolean(this=value)
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PolicyTranslationError("number value expected")
        return exp.Literal.number(value)
    if kind == "string":
        if not isinstance(value, str):
            raise PolicyTranslationError("string value expected")
        return exp.Literal.string(value)
    if kind in ("date", "datetime"):
        text = value.isoformat() if isinstance(value, (date, datetime)) else value
        if not isinstance(text, str):
            raise PolicyTranslationError("date value expected")
        try:
            (date.fromisoformat if kind == "date" else datetime.fromisoformat)(text)
        except ValueError:
            raise PolicyTranslationError("invalid date value") from None
        target = "DATE" if kind == "date" else ("DATETIME2" if dialect == "tsql" else "TIMESTAMP")
        return exp.Cast(this=exp.Literal.string(text), to=exp.DataType.build(target, dialect=dialect))
    raise PolicyTranslationError("unsupported value type")


def _infer(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    raise PolicyTranslationError("unsupported value")
