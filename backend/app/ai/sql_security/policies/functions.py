"""The SQL function allowlist.

A function is allowed only when its SQLGlot key (the parsed function class,
``exp.Anonymous`` name for functions SQLGlot does not model) is approved for
the query's dialect. Appearing inside a SELECT list never makes a function
safe. Everything else is rejected, and the denylist below gives known
dangerous functions (file and network access, dynamic SQL, system and
administrative functions, sleeps and locks) their own violation category.

* ``BASE_FUNCTIONS``: analytical functions safe in every supported dialect
  (COUNT, SUM, AVG, MIN, MAX, ROUND, COALESCE, NULLIF, CASE, CAST, ABS, ...).
* ``DIALECT_FUNCTIONS``: date and string functions approved per dialect. A
  function SQLGlot parses into a portable class (for example ``YEAR`` into
  ``exp.Year``) is approved per dialect all the same.
* ``WINDOW_FUNCTIONS``: only with ``AI_SQL_GUARD_ALLOW_WINDOW_FUNCTIONS``.
* ``AI_SQL_GUARD_EXTRA_FUNCTIONS`` adds ``dialect:key`` or ``*:key`` entries;
  it can never re-enable a function on the denylist.
"""

from sqlglot import exp

# Parsed node classes that are operators or syntax, not functions to approve.
STRUCTURAL_FUNCTION_TYPES: tuple[type[exp.Expression], ...] = (exp.And, exp.Or, exp.Case, exp.If, exp.Cast, exp.Exists)

BASE_FUNCTIONS = frozenset(
    {"count", "sum", "avg", "min", "max", "round", "coalesce", "nullif", "abs", "floor", "ceil", "greatest", "least"}
)

DIALECT_FUNCTIONS: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        {
            "timestamptrunc", "datetrunc", "extract", "currentdate", "currenttimestamp", "date",
            "lower", "upper", "length", "trim", "substring", "concat", "year", "month", "day", "quarter",
        }
    ),
    "mysql": frozenset(
        {
            "timetostr", "tsordstodate", "tsordstotimestamp", "date", "year", "month", "day", "quarter",
            "dateadd", "datesub", "datediff", "currentdate", "currenttimestamp", "extract",
            "lower", "upper", "length", "trim", "substring", "concat",
        }
    ),
    "tsql": frozenset(
        {
            "year", "month", "day", "dateadd", "datediff", "datefromparts", "currentdate", "currenttimestamp",
            "lower", "upper", "length", "trim", "substring", "concat",
        }
    ),
}

WINDOW_FUNCTIONS = frozenset({"rownumber", "rank", "denserank", "lag", "lead", "percentrank", "cumedist", "firstvalue", "lastvalue"})

# Never allowed, whatever the configuration says.
DENIED_FUNCTIONS = frozenset(
    {
        # dynamic SQL and procedure execution
        "sp_executesql", "exec", "execute", "prepare", "deallocate", "query_to_xml", "query_to_xml_and_xmlschema",
        # file access
        "load_file", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export",
        "lo_get", "openrowset", "opendatasource", "bulk", "xp_dirtree", "xp_fileexist", "xp_subdirs",
        # network and external data access
        "dblink", "dblink_exec", "dblink_connect", "openquery", "openxml", "http_get", "utl_http", "xp_cmdshell",
        # system and administrative
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "set_config", "current_setting",
        "pg_backend_pid", "version", "currentversion", "currentuser", "sessionuser", "user", "system_user",
        "database", "currentdatabase", "currentschema", "sys_exec", "sys_eval", "xp_regread", "xp_regwrite",
        "sp_oacreate", "sp_oamethod", "sp_configure", "host_name", "suser_sname", "is_srvrolemember",
        # side effects, sleeps and locks
        "pg_sleep", "pg_sleep_for", "pg_sleep_until", "sleep", "benchmark", "waitfor", "get_lock", "release_lock",
        "pg_advisory_lock", "pg_advisory_xact_lock", "nextval", "setval", "txid_current", "copy",
        # non-deterministic or randomness
        "rand", "random", "newid", "uuid", "gen_random_uuid",
    }
)


def function_key(node: exp.Func) -> str:
    """The lower-case identity of a parsed function: SQLGlot class key, or the name SQLGlot did not model."""
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.key.lower()


def display_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    try:
        return node.sql_name().upper()
    except (AttributeError, ValueError):
        return node.key.upper()


class FunctionPolicy:
    def __init__(self, dialect: str, *, allow_window_functions: bool, extra: frozenset[str] = frozenset()) -> None:
        allowed = set(BASE_FUNCTIONS) | set(DIALECT_FUNCTIONS.get(dialect, frozenset()))
        if allow_window_functions:
            allowed |= WINDOW_FUNCTIONS
        for entry in extra:
            scope, _, name = entry.partition(":")
            if name and scope in ("*", dialect):
                allowed.add(name)
        self.dialect = dialect
        self.allow_window_functions = allow_window_functions
        self.allowed = frozenset(allowed - DENIED_FUNCTIONS)

    def is_denied(self, key: str) -> bool:
        return key in DENIED_FUNCTIONS

    def is_window(self, key: str) -> bool:
        return key in WINDOW_FUNCTIONS

    def is_allowed(self, key: str) -> bool:
        return key in self.allowed
