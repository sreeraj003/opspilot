"""Builds the normalised, bounded result table from a validated company API result.

``normalize_result`` never invents or changes data:

* columns the approved query did not select are removed (``withheld_columns``)
  and reported, never shown;
* every remaining value is normalised by its column's type (see ``types``),
  or the whole result is refused;
* rows beyond ``max_rows`` or beyond ``max_result_bytes`` of normalised JSON
  are dropped *from the end* and the result says so (``truncated``,
  ``truncation_reasons``, ``rows_received`` versus ``row_count``);
* ``limit_reached`` tells that the approved query's own row limit was reached,
  so more matching rows may exist in the company database.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from app.ai.query_execution.normalizers.types import ColumnType, column_type, normalize_value
from app.ai.query_execution.policies.limits import ExecutionLimits
from app.ai.query_execution.validators.projection import ApprovedProjection
from app.ai.query_execution.validators.response import validate_table


@dataclass(frozen=True, slots=True)
class ResultColumn:
    name: str
    data_type: str
    nullable: bool
    timezone: str | None = None
    type_source: str = "inferred"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "data_type": self.data_type, "nullable": self.nullable, "timezone": self.timezone,
                "type_source": self.type_source}


@dataclass(slots=True)
class NormalizedTable:
    columns: list[ResultColumn]
    rows: list[dict[str, Any]]
    rows_received: int
    truncated: bool = False
    truncation_reasons: list[str] = field(default_factory=list)
    withheld_columns: list[str] = field(default_factory=list)
    query_row_limit: int | None = None
    limit_reached: bool = False
    normalized_size_bytes: int = 2

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def normalize_result(
    rows: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None,
    limits: ExecutionLimits,
    projection: ApprovedProjection | None,
) -> NormalizedTable:
    columns, declared = validate_table(rows, metadata, limits)
    withheld: list[str] = []
    if projection is not None:
        columns, withheld = projection.split(columns)
    types: dict[str, ColumnType] = {
        name: column_type(declared.get(name), [row[name] for row in rows]) for name in columns
    }
    kept: list[dict[str, Any]] = []
    reasons: list[str] = []
    size = 2  # "[]"
    for index, row in enumerate(rows):
        normalized = {name: normalize_value(row[name], types[name], name) for name in columns}
        if index >= limits.max_rows:
            reasons.append("row_limit")
            break
        row_size = _size(normalized) + 1
        if size + row_size > limits.max_result_bytes:
            reasons.append("size_limit")
            break
        size += row_size
        kept.append(normalized)
    # Values of dropped rows are still validated above up to the first dropped one; validate the rest too,
    # so a truncated result never hides malformed data.
    for row in rows[len(kept) + 1:]:
        for name in columns:
            normalize_value(row[name], types[name], name)
    result_columns = [
        ResultColumn(name, types[name].data_type, any(row[name] is None for row in rows), types[name].timezone,
                     types[name].source)
        for name in columns
    ]
    limit = projection.row_limit if projection else None
    return NormalizedTable(
        columns=result_columns,
        rows=kept,
        rows_received=len(rows),
        truncated=bool(reasons),
        truncation_reasons=reasons,
        withheld_columns=withheld,
        query_row_limit=limit,
        limit_reached=limit is not None and len(rows) >= limit,
        normalized_size_bytes=size,
    )
