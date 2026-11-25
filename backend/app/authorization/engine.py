"""The authorization engine: effective permissions and allow/deny decisions.

Independent of FastAPI and of any AI component: it takes a database session
and returns typed decisions. Only this module creates ``AuthorizationDecision``
objects; no API accepts one as input.

Resolution pipeline (``docs/PERMISSION_POLICY.md`` has the details)::

    user (must be live and active)
      -> active, unexpired role assignments of active roles
      -> active, unexpired permissions of those roles
      +  active, unexpired direct user permissions (grant or deny)
      -> effective grants (direct "deny" assignments remove a permission entirely)
      -> grants matching the requested resource type and action
      -> deny policies applied after every allow (they always win)
      -> decision: allowed tables, columns, operations, row filters, export, SQL visibility

Precedence (no last-write-wins anywhere):

1. Inactive, deleted or unknown users are denied everything.
2. Missing permissions mean denial (deny by default).
3. Revoked or expired assignments, inactive or expired permissions and
   inactive roles contribute nothing.
4. A direct ``deny`` assignment removes that permission, whatever grants it.
5. Deny-effect permissions (from roles or direct grants) are applied after all
   allows: denied tables/columns are removed, excluded rows are added as
   mandatory restrictions, export / SQL visibility can be switched off. No
   grant, including a direct one, can lift them.
6. Several allow grants combine with OR: a request is allowed if at least one
   grant covers every requested column of a table; the row filters of all
   grants that do are OR-ed, then mandatory exclusions are AND-ed on top.
7. Administrator-only system permissions count only when held through the
   protected ``administrator`` role or by the installation administrator.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.cache import AuthorizationCache
from app.authorization.policy import (
    ALLOW,
    DATA_RESOURCE_TYPE,
    DENY,
    merge_override,
    node_table,
    split_column,
)
from app.core.authorization import (
    ADMINISTRATOR_ONLY_PERMISSIONS,
    ADMINISTRATOR_ROLE_KEY,
    SYSTEM_RESOURCE_TYPE,
    Permission,
    is_live,
)
from app.models.rbac import AssignmentType
from app.models.user import User
from app.repositories.rbac_repository import AssignmentRepository, AuthorizationStateRepository
from app.repositories.user_repository import UserRepository

NOT_AN_AUTHORIZATION_TOKEN = (
    "Informational description of the user's permissions. It is not an authorization token: "
    "every request is authorized again by the server."
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _min_expiry(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


# --- resolved (cacheable) state ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleRef:
    id: uuid.UUID
    key: str
    name: str
    is_system: bool
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Grant:
    """One permission held by the user through one source."""

    permission_id: uuid.UUID
    key: str
    name: str
    resource_type: str
    action: str
    policy: dict[str, Any]  # normalised; for a direct grant, already narrowed by its override
    is_system: bool
    source: str  # "role" or "direct"
    role_key: str | None
    assignment_type: str  # grant / deny (role grants are always "grant")
    expires_at: datetime | None

    @property
    def effect(self) -> str:
        return str(self.policy.get("effect", ALLOW))

    def active(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True, slots=True)
class ResolvedAccess:
    user_id: uuid.UUID
    revision: int
    roles: tuple[RoleRef, ...]
    grants: tuple[Grant, ...]


# --- effective state at a moment -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    user_id: uuid.UUID
    revision: int
    evaluated_at: datetime
    is_administrator: bool
    roles: tuple[RoleRef, ...]
    allows: tuple[Grant, ...]  # allow-effect grants after direct denials were removed
    denies: tuple[Grant, ...]  # deny-effect grants (mandatory restrictions)
    revoked_keys: frozenset[str]  # permissions removed by a direct "deny" assignment
    system_permissions: frozenset[Permission]

    @property
    def permission_keys(self) -> list[str]:
        return sorted({g.key for g in self.allows})


EMPTY_ROLES: tuple[RoleRef, ...] = ()


def effective_from(access: ResolvedAccess, user: User, now: datetime) -> EffectiveAccess:
    if not is_live(user):
        return EffectiveAccess(user.id, access.revision, now, False, (), (), (), frozenset(), frozenset())
    roles = tuple(r for r in access.roles if r.expires_at is None or r.expires_at > now)
    grants = [g for g in access.grants if g.active(now)]
    is_admin = user.is_superuser or any(r.key == ADMINISTRATOR_ROLE_KEY and r.is_system for r in roles)
    revoked = frozenset(g.key for g in grants if g.assignment_type == AssignmentType.DENY)
    holding = [g for g in grants if g.assignment_type == AssignmentType.GRANT]
    allows = tuple(g for g in holding if g.effect == ALLOW and g.key not in revoked)
    denies = tuple(g for g in holding if g.effect == DENY)

    system: set[Permission] = set()
    if user.is_superuser:
        system = set(Permission)  # break-glass: the installation administrator can never be locked out
    else:
        for grant in allows:
            if grant.resource_type != SYSTEM_RESOURCE_TYPE or grant.key not in Permission._value2member_map_:
                continue
            via_admin_role = grant.source == "role" and grant.role_key == ADMINISTRATOR_ROLE_KEY
            if grant.key in ADMINISTRATOR_ONLY_PERMISSIONS and not via_admin_role:
                continue  # defence in depth: never honoured outside the administrator role
            system.add(Permission(grant.key))
    return EffectiveAccess(
        user_id=user.id,
        revision=access.revision,
        evaluated_at=now,
        is_administrator=is_admin,
        roles=roles,
        allows=allows,
        denies=denies,
        revoked_keys=revoked,
        system_permissions=frozenset(system),
    )


# --- requests and decisions --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """What a trusted backend component wants to do on behalf of ``user_id``."""

    user_id: uuid.UUID
    resource_type: str
    action: str
    tables: tuple[str, ...] = ()
    # ``table.column`` (or a bare column when exactly one table is requested).
    columns: tuple[str, ...] = ()
    operation: str | None = None  # "read" or "aggregate" for database_data
    export: bool = False
    sql_view: bool = False


@dataclass(frozen=True, slots=True)
class RowFilter:
    table: str
    filter: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TableAccess:
    table: str
    columns: tuple[str, ...]  # qualified; "table.*" means every column not in denied_columns
    denied_columns: tuple[str, ...]
    operations: tuple[str, ...]
    row_filter: dict[str, Any] | None
    allow_export: bool
    allow_sql_view: bool
    permission_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """A trusted decision. Created only by ``AuthorizationEngine``."""

    allowed: bool
    decision_reason: str
    user_id: uuid.UUID | None
    resource: str
    action: str
    policy_revision: int
    evaluated_at: datetime
    allowed_tables: tuple[str, ...] = ()
    allowed_columns: tuple[str, ...] = ()
    denied_columns: tuple[str, ...] = ()
    allowed_operations: tuple[str, ...] = ()
    row_filters: tuple[RowFilter, ...] = ()
    table_access: tuple[TableAccess, ...] = ()
    allow_export: bool = False
    allow_sql_view: bool = False
    matched_permissions: tuple[str, ...] = ()


def _deny(request: AuthorizationRequest, effective: EffectiveAccess | None, reason: str, now: datetime) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        decision_reason=reason,
        user_id=request.user_id,
        resource=request.resource_type,
        action=request.action,
        policy_revision=effective.revision if effective else 0,
        evaluated_at=now,
    )


# --- data scopes -------------------------------------------------------------------------------------


@dataclass
class _Scope:
    """One allow grant's access to one table."""

    table: str
    all_columns: bool
    columns: set[str]
    filters: list[dict[str, Any]]
    operations: set[str]
    allow_export: bool
    allow_sql_view: bool
    key: str

    def covers(self, columns: set[str], excluded: set[str]) -> bool:
        if columns & excluded:
            return False
        return self.all_columns or columns <= self.columns

    def filter_node(self) -> dict[str, Any] | None:
        if not self.filters:
            return None
        return self.filters[0] if len(self.filters) == 1 else {"logic": "and", "conditions": list(self.filters)}


@dataclass
class _DataView:
    scopes: dict[str, list[_Scope]] = field(default_factory=dict)
    excluded_columns: dict[str, set[str]] = field(default_factory=dict)
    exclusions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    deny_export: bool = False
    deny_sql_view: bool = False
    deny_keys: set[str] = field(default_factory=set)


def _data_view(allows: Sequence[Grant], denies: Sequence[Grant]) -> tuple[_DataView, str | None]:
    """Scopes per table after mandatory restrictions; second item is a deny-all reason."""
    view = _DataView()
    for grant in denies:
        policy = grant.policy
        view.deny_keys.add(grant.key)
        if policy.get("deny_all"):
            return view, f"Access is explicitly denied by permission '{grant.key}'."
        for table in policy.get("denied_tables", []):
            view.excluded_columns.setdefault(table, set()).add("*")
        for column in policy.get("denied_columns", []):
            table, name = split_column(column)
            view.excluded_columns.setdefault(table, set()).add(name)
        for node in policy.get("excluded_rows", []):
            view.exclusions.setdefault(node_table(node), []).append(node)
        view.deny_export |= bool(policy.get("deny_export"))
        view.deny_sql_view |= bool(policy.get("deny_sql_view"))
    for grant in allows:
        policy = grant.policy
        for table in policy.get("allowed_tables", []):
            if "*" in view.excluded_columns.get(table, set()):
                continue
            columns = {split_column(c)[1] for c in policy.get("allowed_columns", []) if split_column(c)[0] == table}
            if not columns or ("*" not in columns and not columns - view.excluded_columns.get(table, set())):
                continue  # nothing left of this table once denied columns are removed
            all_columns = "*" in columns
            view.scopes.setdefault(table, []).append(
                _Scope(
                    table=table,
                    all_columns=all_columns,
                    columns=columns - {"*"},
                    filters=[f for f in policy.get("row_filters", []) if node_table(f) == table],
                    operations=set(policy.get("allowed_operations", [])),
                    allow_export=bool(policy.get("allow_export")),
                    allow_sql_view=bool(policy.get("allow_sql_view")),
                    key=grant.key,
                )
            )
    return view, None


def _combine(scopes: Sequence[_Scope], exclusions: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """OR of the scopes' filters (None if any scope is unrestricted), AND NOT each exclusion."""
    nodes = [s.filter_node() for s in scopes]
    allow_node: dict[str, Any] | None
    if any(n is None for n in nodes):
        allow_node = None
    else:
        unique: list[dict[str, Any]] = []
        for node in nodes:
            if node is not None and node not in unique:
                unique.append(node)
        allow_node = unique[0] if len(unique) == 1 else {"logic": "or", "conditions": unique}
    parts: list[dict[str, Any]] = [] if allow_node is None else [allow_node]
    parts.extend({"logic": "not", "conditions": [node]} for node in exclusions)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"logic": "and", "conditions": parts}


def _table_access(table: str, scopes: Sequence[_Scope], view: _DataView) -> TableAccess:
    excluded = view.excluded_columns.get(table, set())
    all_columns = any(s.all_columns for s in scopes)
    if all_columns:
        columns = (f"{table}.*",)
    else:
        columns = tuple(sorted(f"{table}.{c}" for s in scopes for c in s.columns if c not in excluded))
        columns = tuple(dict.fromkeys(columns))
    return TableAccess(
        table=table,
        columns=columns,
        denied_columns=tuple(sorted(f"{table}.{c}" for c in excluded)),
        operations=tuple(sorted({o for s in scopes for o in s.operations})),
        row_filter=_combine(scopes, view.exclusions.get(table, [])),
        allow_export=any(s.allow_export for s in scopes) and not view.deny_export,
        allow_sql_view=any(s.allow_sql_view for s in scopes) and not view.deny_sql_view,
        permission_keys=tuple(sorted({s.key for s in scopes})),
    )


def evaluate(effective: EffectiveAccess, request: AuthorizationRequest) -> AuthorizationDecision:
    """Pure decision function over already-resolved effective access."""
    now = effective.evaluated_at
    if effective.user_id != request.user_id:
        return _deny(request, effective, "The request does not match the evaluated user.", now)

    if request.resource_type == SYSTEM_RESOURCE_TYPE:
        allowed = request.action in {p.value for p in effective.system_permissions}
        return AuthorizationDecision(
            allowed=allowed,
            decision_reason=(
                "Granted by the administrative policy." if allowed else "No applicable permission was found."
            ),
            user_id=request.user_id,
            resource=request.resource_type,
            action=request.action,
            policy_revision=effective.revision,
            evaluated_at=now,
            matched_permissions=(request.action,) if allowed else (),
        )

    allows = [g for g in effective.allows if g.resource_type == request.resource_type and g.action == request.action]
    denies = [g for g in effective.denies if g.resource_type == request.resource_type and g.action == request.action]
    if not allows:
        return _deny(request, effective, "No applicable permission was found.", now)

    if request.resource_type != DATA_RESOURCE_TYPE:
        for grant in denies:
            if grant.policy.get("deny_all"):
                return _deny(request, effective, f"Access is explicitly denied by permission '{grant.key}'.", now)
        deny_export = any(g.policy.get("deny_export") for g in denies)
        deny_sql = any(g.policy.get("deny_sql_view") for g in denies)
        allow_export = any(g.policy.get("allow_export") for g in allows) and not deny_export
        allow_sql = any(g.policy.get("allow_sql_view") for g in allows) and not deny_sql
        if request.export and not allow_export:
            return _deny(request, effective, "Export is not permitted.", now)
        if request.sql_view and not allow_sql:
            return _deny(request, effective, "Viewing SQL is not permitted.", now)
        return AuthorizationDecision(
            allowed=True,
            decision_reason=_granted_reason(allows),
            user_id=request.user_id,
            resource=request.resource_type,
            action=request.action,
            policy_revision=effective.revision,
            evaluated_at=now,
            allow_export=allow_export,
            allow_sql_view=allow_sql,
            matched_permissions=tuple(sorted({g.key for g in allows})),
        )

    view, deny_all = _data_view(allows, denies)
    if deny_all:
        return _deny(request, effective, deny_all, now)
    if not view.scopes:
        return _deny(request, effective, "Every table this permission covers is explicitly denied.", now)

    if request.export and view.deny_export:
        return _deny(request, effective, "Export is explicitly denied.", now)
    if request.sql_view and view.deny_sql_view:
        return _deny(request, effective, "Viewing SQL is explicitly denied.", now)

    if not request.tables:
        if request.columns:
            return _deny(request, effective, "Columns were requested without naming their tables.", now)
        accesses = [_table_access(t, s, view) for t, s in sorted(view.scopes.items())]
        return _allowed_data(request, effective, accesses, allows, now)

    accesses: list[TableAccess] = []
    requested = _requested_columns(request)
    if isinstance(requested, str):
        return _deny(request, effective, requested, now)
    for table in request.tables:
        scopes = view.scopes.get(table)
        if not scopes:
            return _deny(request, effective, f"Access to table '{table}' is not permitted.", now)
        wanted = requested.get(table, set())
        excluded = view.excluded_columns.get(table, set())
        blocked = sorted(wanted & excluded)
        if blocked:
            return _deny(request, effective, f"Column '{table}.{blocked[0]}' is explicitly denied.", now)
        candidates = [
            s
            for s in scopes
            if s.covers(wanted, excluded)
            and (request.operation is None or request.operation in s.operations)
            and (not request.export or s.allow_export)
            and (not request.sql_view or s.allow_sql_view)
        ]
        if not candidates:
            return _deny(request, effective, _why_not(table, scopes, wanted, request), now)
        accesses.append(_table_access(table, candidates, view))
    return _allowed_data(request, effective, accesses, allows, now)


def _requested_columns(request: AuthorizationRequest) -> dict[str, set[str]] | str:
    result: dict[str, set[str]] = {}
    for column in request.columns:
        table, name = split_column(column)
        if not table:
            if len(request.tables) != 1:
                return f"Qualify column '{column}' with its table."
            table = request.tables[0]
        if table not in request.tables:
            return f"Column '{column}' belongs to a table that was not requested."
        result.setdefault(table, set()).add(name)
    return result


def _why_not(table: str, scopes: Sequence[_Scope], wanted: set[str], request: AuthorizationRequest) -> str:
    if not any(s.covers(wanted, set()) for s in scopes):
        missing = sorted(c for c in wanted if not any(s.all_columns or c in s.columns for s in scopes))
        if missing:
            return f"Column '{table}.{missing[0]}' is not permitted."
        return f"No single permission covers all requested columns of '{table}'."
    if request.operation and not any(request.operation in s.operations for s in scopes):
        return f"The '{request.operation}' operation is not permitted on '{table}'."
    if request.export:
        return "Export is not permitted."
    if request.sql_view:
        return "Viewing SQL is not permitted."
    return f"No permission covers this request on '{table}'."


def _granted_reason(allows: Sequence[Grant]) -> str:
    sources = {g.source for g in allows}
    if sources == {"role"}:
        return "Permission granted through assigned role."
    if sources == {"direct"}:
        return "Permission granted directly to the user."
    return "Permission granted through assigned role and direct permission."


def _allowed_data(
    request: AuthorizationRequest,
    effective: EffectiveAccess,
    accesses: Sequence[TableAccess],
    allows: Sequence[Grant],
    now: datetime,
) -> AuthorizationDecision:
    keys = {k for a in accesses for k in a.permission_keys}
    return AuthorizationDecision(
        allowed=True,
        decision_reason=_granted_reason([g for g in allows if g.key in keys]),
        user_id=request.user_id,
        resource=request.resource_type,
        action=request.action,
        policy_revision=effective.revision,
        evaluated_at=now,
        allowed_tables=tuple(a.table for a in accesses),
        allowed_columns=tuple(c for a in accesses for c in a.columns),
        denied_columns=tuple(c for a in accesses for c in a.denied_columns),
        allowed_operations=tuple(sorted({o for a in accesses for o in a.operations})),
        row_filters=tuple(RowFilter(a.table, a.row_filter) for a in accesses if a.row_filter is not None),
        table_access=tuple(accesses),
        allow_export=all(a.allow_export for a in accesses),
        allow_sql_view=all(a.allow_sql_view for a in accesses),
        matched_permissions=tuple(sorted(keys)),
    )


# --- AI authorization context -------------------------------------------------------------------------


def ai_context(effective: EffectiveAccess) -> dict[str, Any]:
    """Structured, minimal description of what the user may ask about.

    Contains no credentials, session data or administrative information. It is
    not an authorization token: the SQL security guard (Module 11) authorizes
    every query again through ``AuthorizationEngine.authorize``.
    """
    business = [g for g in effective.allows if g.resource_type != SYSTEM_RESOURCE_TYPE]
    pairs = sorted({(g.resource_type, g.action) for g in business})
    resources: list[dict[str, Any]] = []
    data_access: list[dict[str, Any]] = []
    for resource_type, action in pairs:
        request = AuthorizationRequest(effective.user_id, resource_type, action)
        decision = evaluate(effective, request)
        if not decision.allowed:
            continue
        if resource_type == DATA_RESOURCE_TYPE:
            data_access.append(
                {
                    "action": action,
                    "tables": [
                        {
                            "table": a.table,
                            "columns": list(a.columns),
                            "denied_columns": list(a.denied_columns),
                            "operations": list(a.operations),
                            "row_filter": a.row_filter,
                            "allow_export": a.allow_export,
                            "allow_sql_view": a.allow_sql_view,
                        }
                        for a in decision.table_access
                    ],
                }
            )
        else:
            resources.append(
                {
                    "resource_type": resource_type,
                    "action": action,
                    "allow_export": decision.allow_export,
                    "allow_sql_view": decision.allow_sql_view,
                }
            )
    tables = sorted({t["table"] for d in data_access for t in d["tables"]})
    columns = sorted({c for d in data_access for t in d["tables"] for c in t["columns"]})
    return {
        "user_id": str(effective.user_id),
        "policy_revision": effective.revision,
        "generated_at": effective.evaluated_at.isoformat(),
        "roles": [{"key": r.key, "name": r.name} for r in effective.roles],
        "permission_keys": sorted({g.key for g in business}),
        "allowed_resources": sorted({r for r, _ in pairs}),
        "allowed_actions": [{"resource_type": r, "action": a} for r, a in pairs],
        "resources": resources,
        "data_access": data_access,
        "allowed_tables": tables,
        "allowed_columns": columns,
        "export_allowed": any(t["allow_export"] for d in data_access for t in d["tables"])
        or any(r["allow_export"] for r in resources),
        "sql_view_allowed": any(t["allow_sql_view"] for d in data_access for t in d["tables"])
        or any(r["allow_sql_view"] for r in resources),
        "is_authorization_token": False,
        "notice": NOT_AN_AUTHORIZATION_TOKEN,
    }


# --- the engine -------------------------------------------------------------------------------------


class AuthorizationEngine:
    def __init__(self, session: AsyncSession, cache: AuthorizationCache | None = None) -> None:
        self._session = session
        self._cache = cache
        self._assignments = AssignmentRepository(session)
        self._state = AuthorizationStateRepository(session)
        self._users = UserRepository(session)

    async def revision(self) -> int:
        return await self._state.current_revision()

    async def resolve_many(
        self, user_ids: Sequence[uuid.UUID], *, fresh: bool = False, now: datetime | None = None
    ) -> dict[uuid.UUID, ResolvedAccess]:
        """Grants of many users in three queries (never one query per user or per role)."""
        now = now or utcnow()
        revision = await self._state.current_revision()
        result: dict[uuid.UUID, ResolvedAccess] = {}
        missing: list[uuid.UUID] = []
        for user_id in dict.fromkeys(user_ids):
            cached = None if fresh or self._cache is None else self._cache.get(user_id, revision)
            if cached is not None:
                result[user_id] = cached
            else:
                missing.append(user_id)
        if not missing:
            return result
        roles: dict[uuid.UUID, list[RoleRef]] = {u: [] for u in missing}
        grants: dict[uuid.UUID, list[Grant]] = {u: [] for u in missing}
        for row in await self._assignments.load_roles(missing, now):
            user_id, expires_at, role_id, key, name, is_system = row
            roles[user_id].append(RoleRef(role_id, key, name, is_system, expires_at))
        for row in await self._assignments.load_role_grants(missing, now):
            user_id, role_expires, _role_id, role_key, _role_system, permission = row
            grants[user_id].append(
                Grant(
                    permission_id=permission.id,
                    key=permission.key,
                    name=permission.name,
                    resource_type=permission.resource_type,
                    action=permission.action,
                    policy=permission.policy_definition,
                    is_system=permission.is_system,
                    source="role",
                    role_key=role_key,
                    assignment_type=AssignmentType.GRANT,
                    expires_at=_min_expiry(role_expires, permission.expires_at),
                )
            )
        for assignment, permission in await self._assignments.load_direct_grants(missing, now):
            policy = permission.policy_definition
            if assignment.assignment_type == AssignmentType.GRANT:
                policy = merge_override(policy, assignment.policy_override)
            grants[assignment.user_id].append(
                Grant(
                    permission_id=permission.id,
                    key=permission.key,
                    name=permission.name,
                    resource_type=permission.resource_type,
                    action=permission.action,
                    policy=policy,
                    is_system=permission.is_system,
                    source="direct",
                    role_key=None,
                    assignment_type=assignment.assignment_type,
                    expires_at=_min_expiry(assignment.expires_at, permission.expires_at),
                )
            )
        for user_id in missing:
            resolved = ResolvedAccess(
                user_id=user_id,
                revision=revision,
                roles=tuple(roles[user_id]),
                grants=tuple(sorted(grants[user_id], key=lambda g: (g.key, g.source, g.role_key or ""))),
            )
            if self._cache is not None:
                self._cache.put(resolved)
            result[user_id] = resolved
        return result

    async def effective(self, user: User, *, fresh: bool = False, now: datetime | None = None) -> EffectiveAccess:
        now = now or utcnow()
        if not is_live(user):
            return effective_from(ResolvedAccess(user.id, 0, (), ()), user, now)
        access = (await self.resolve_many([user.id], fresh=fresh, now=now))[user.id]
        return effective_from(access, user, now)

    async def system_permissions(self, user: User) -> frozenset[Permission]:
        if user.is_superuser and is_live(user):
            return frozenset(Permission)  # no database round trip for the installation administrator
        if not is_live(user):
            return frozenset()
        return (await self.effective(user)).system_permissions

    async def authorize(self, request: AuthorizationRequest, *, fresh: bool = False) -> AuthorizationDecision:
        """Re-reads the user (identity and status) and decides. ``fresh`` bypasses the cache entirely."""
        now = utcnow()
        user = await self._users.get(request.user_id)
        if user is None:
            return _deny(request, None, "The user does not exist.", now)
        if not is_live(user):
            return _deny(request, None, "The user account is inactive.", now)
        effective = await self.effective(user, fresh=fresh, now=now)
        return evaluate(effective, request)

    async def ai_authorization_context(self, user: User) -> dict[str, Any]:
        """See ``ai_context``. Callers must only pass the authenticated user, or act for an
        administrator who holds ``access:review``."""
        return ai_context(await self.effective(user))


class EngineAuthorizationPolicy:
    """``AuthorizationPolicy`` backed by the engine (replaces the Module 03 bootstrap policy).

    Opens its own short session only when a database lookup is needed, so the
    installation administrator works even without one.
    """

    def __init__(self, database: Any, cache: AuthorizationCache | None) -> None:
        self._database = database
        self._cache = cache

    async def permissions_for(self, user: User) -> frozenset[Permission]:
        if not is_live(user):
            return frozenset()
        if user.is_superuser:
            return frozenset(Permission)
        if self._database is None:
            return frozenset()  # fail closed
        async with self._database.session() as session:
            return await AuthorizationEngine(session, self._cache).system_permissions(user)

    async def is_allowed(self, user: User, permission: Permission) -> bool:
        return permission in await self.permissions_for(user)
