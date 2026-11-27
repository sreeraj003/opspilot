"""Resource limits for one execution and its result (``AI_SQL_EXEC_*`` settings).

Policy (documented in docs/QUERY_EXECUTION.md):

* **Response bytes**: the body is streamed and the request fails as soon as it
  exceeds ``max_response_bytes`` (or the connector's lower limit). Nothing
  larger is ever held in memory.
* **Duration**: the connector's timeouts are cut to ``max_duration_seconds``.
* **Rows**: rows beyond ``max_rows`` are dropped and the result is marked
  truncated with the number of rows received and kept.
* **Normalised size**: rows beyond ``max_result_bytes`` of normalised JSON are
  dropped the same way (reason ``size_limit``).
* **Columns, cell length, nesting depth**: exceeding them rejects the result.
  Columns and values are never cut, because a partial value would be a
  fabricated one.
* **Concurrency**: at most ``max_concurrent_per_user`` executions in flight per
  user.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    max_response_bytes: int = 5_242_880
    max_duration_seconds: int = 30
    max_rows: int = 1000
    max_columns: int = 100
    max_cell_length: int = 10000
    max_nesting_depth: int = 5
    max_result_bytes: int = 2_097_152
    max_concurrent_per_user: int = 2
    result_retention_minutes: int = 60

    @classmethod
    def from_settings(cls, settings: object) -> "ExecutionLimits":
        s = settings
        return cls(
            max_response_bytes=s.ai_sql_exec_max_response_bytes,  # type: ignore[attr-defined]
            max_duration_seconds=s.ai_sql_exec_max_duration_seconds,  # type: ignore[attr-defined]
            max_rows=s.ai_sql_exec_max_rows,  # type: ignore[attr-defined]
            max_columns=s.ai_sql_exec_max_columns,  # type: ignore[attr-defined]
            max_cell_length=s.ai_sql_exec_max_cell_length,  # type: ignore[attr-defined]
            max_nesting_depth=s.ai_sql_exec_max_nesting_depth,  # type: ignore[attr-defined]
            max_result_bytes=s.ai_sql_exec_max_result_bytes,  # type: ignore[attr-defined]
            max_concurrent_per_user=s.ai_sql_exec_max_concurrent_per_user,  # type: ignore[attr-defined]
            result_retention_minutes=s.ai_sql_exec_result_retention_minutes,  # type: ignore[attr-defined]
        )

    @property
    def stale_after_seconds(self) -> int:
        """An execution still pending or running after this long can no longer be in flight."""
        return self.max_duration_seconds + 60
