"""The user's effective data permissions, seen against one registered schema.

Built from ``AuthorizationEngine.ai_authorization_context`` (Module 04), which
is context, not authorization. This view only decides *what the model is shown*
and lets the backend notice early that a question needs restricted data. The
binding decision is ``IntentAuthorizer`` (in ``understanding_service``), which
asks the engine again for the exact tables, columns and operation an intent
needs; the SQL security module will authorize once more at execution time.

Policy tables are named ``table`` or ``schema.table``; a registered table
matches either form, case-insensitively. Every ``database_data`` action the
user holds counts: Module 04 does not reserve a particular action name for
querying.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.ai.understanding.services.schema_context import ColumnInfo, SchemaContext, TableInfo
from app.authorization.engine import AuthorizationDecision, AuthorizationEngine, AuthorizationRequest
from app.authorization.policy import DATA_RESOURCE_TYPE


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    action: str
    policy_table: str  # the table name as the permission policy writes it
    all_columns: bool
    columns: frozenset[str]  # lower-case column names
    spelling: dict[str, str]  # lower-case column name -> as the policy writes it
    denied_columns: frozenset[str]
    operations: tuple[str, ...]
    row_filter: dict[str, Any] | None
    allow_export: bool


def _matches(policy_table: str, table: TableInfo) -> bool:
    key = policy_table.lower()
    return key in (table.table_name.lower(), table.qualified.lower())


class PermissionView:
    def __init__(self, ai_context: dict[str, Any], schema: SchemaContext) -> None:
        self.policy_revision = int(ai_context.get("policy_revision", 0))
        self.schema = schema
        self._entries: dict[str, list[PolicyEntry]] = {}
        for access in ai_context.get("data_access", []):
            for item in access.get("tables", []):
                policy_table = str(item["table"])
                prefix = policy_table.lower() + "."
                raw_columns = [str(c) for c in item.get("columns", [])]
                columns = [c.lower() for c in raw_columns]
                entry = PolicyEntry(
                    action=str(access["action"]),
                    policy_table=policy_table,
                    all_columns=f"{prefix}*" in columns,
                    columns=frozenset(c[len(prefix):] for c in columns if c.startswith(prefix) and not c.endswith("*")),
                    spelling={c.lower()[len(prefix):]: c[len(prefix):] for c in raw_columns if c.lower().startswith(prefix)},
                    denied_columns=frozenset(
                        str(c).lower()[len(prefix):] for c in item.get("denied_columns", []) if str(c).lower().startswith(prefix)
                    ),
                    operations=tuple(item.get("operations", [])),
                    row_filter=item.get("row_filter"),
                    allow_export=bool(item.get("allow_export")),
                )
                for table in schema.tables.values():
                    if _matches(policy_table, table):
                        self._entries.setdefault(table.qualified.lower(), []).append(entry)

    @property
    def has_any_data_access(self) -> bool:
        return bool(self._entries)

    def entries(self, qualified_table: str) -> list[PolicyEntry]:
        return self._entries.get(qualified_table.lower(), [])

    def table_allowed(self, qualified_table: str) -> bool:
        return bool(self.entries(qualified_table))

    def column_allowed(self, qualified_table: str, column: str) -> bool:
        name = column.lower()
        return any(
            name not in e.denied_columns and (e.all_columns or name in e.columns) for e in self.entries(qualified_table)
        )

    def visible_columns(self, table: TableInfo) -> list[ColumnInfo]:
        return [c for c in table.columns.values() if self.column_allowed(table.qualified, c.name)]

    def operations(self, qualified_table: str) -> list[str]:
        return sorted({op for e in self.entries(qualified_table) for op in e.operations})

    def export_allowed(self, qualified_table: str) -> bool:
        return any(e.allow_export for e in self.entries(qualified_table))

    def row_filter(self, qualified_table: str) -> dict[str, Any] | None:
        """The rows the user may see: grants are alternatives (OR); an unfiltered grant means all rows."""
        entries = self.entries(qualified_table)
        if not entries or any(e.row_filter is None for e in entries):
            return None
        filters: list[dict[str, Any]] = []
        for entry in entries:
            if entry.row_filter not in filters:
                filters.append(entry.row_filter)  # type: ignore[arg-type]
        return filters[0] if len(filters) == 1 else {"logic": "or", "conditions": filters}

    def actions(self) -> list[str]:
        return sorted({e.action for entries in self._entries.values() for e in entries})

    def policy_table(self, qualified_table: str, action: str) -> str | None:
        return next((e.policy_table for e in self.entries(qualified_table) if e.action == action), None)

    def policy_column(self, qualified_table: str, action: str, column: str) -> str:
        """``policy_table.column`` spelt as the policy spells it (registry spelling for ``table.*`` grants)."""
        for entry in self.entries(qualified_table):
            if entry.action == action:
                return f"{entry.policy_table}.{entry.spelling.get(column.lower(), column)}"
        return f"{qualified_table}.{column}"


async def authorize_references(
    engine: AuthorizationEngine,
    user_id: uuid.UUID,
    permissions: PermissionView,
    referenced: Mapping[str, set[str]],
    operation: str,
    *,
    sql_view: bool = False,
) -> AuthorizationDecision | None:
    """Ask the permission engine for exactly these tables, columns and operation.

    ``referenced`` maps qualified registered tables to column names. Any
    ``database_data`` action the user holds may allow it; the first allowing
    decision is returned, otherwise ``None``. Used by question understanding
    (Module 09) and SQL generation (Module 10); the SQL security guard
    authorizes again at execution time.
    """
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
            decision = await engine.authorize(
                AuthorizationRequest(
                    user_id=user_id, resource_type=DATA_RESOURCE_TYPE, action=action,
                    tables=tuple(tables), columns=tuple(columns), operation=operation, sql_view=sql_view,
                )
            )
            if decision.allowed:
                return decision
    return None
