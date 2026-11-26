"""Detection of executable content in text.

Business intent is data. Nothing that looks like SQL, code, markup or tool
instructions is accepted from the model, whichever field it appears in. The
patterns aim at statement-like fragments, comments and code constructs, not at
ordinary words ("select", "order", "group" on their own are fine).

``question_problem`` applies the statement-level subset to the user's own
question: OpsPilot answers questions in plain language and does not accept SQL
typed by users.
"""

import re

_FLAGS = re.IGNORECASE | re.MULTILINE

# Statement-level SQL and SQL comment syntax.
_SQL_STATEMENT = [
    r"\bselect\b[\s\S]{0,300}?\bfrom\b\s+[\w.\"`\[\]]+",
    r"\binsert\s+into\b",
    r"\bupdate\s+[\w.\"`\[\]]+\s+set\b",
    r"\bdelete\s+from\b",
    r"\bmerge\s+into\b",
    r"\b(drop|alter|truncate|create)\s+(table|database|schema|view|index|function|procedure|trigger|user|role)\b",
    r"\bunion\s+(all\s+)?select\b",
    r"\bgrant\s+(select|insert|update|delete|all)\b",
    r"\brevoke\s+(select|insert|update|delete|all)\b",
    r"\bexec(ute)?\s*\(",
    r"\bexec(ute)?\s+(sp_|xp_|immediate\b)",
    r";\s*(--|#|/\*)",
    r"--\s*$",
    r"/\*|\*/",
    r"\bxp_cmdshell\b",
    r"\bpg_sleep\s*\(",
    r"\bwaitfor\s+delay\b",
    r"\b(information_schema|pg_catalog|sys\.objects|sqlite_master)\b",
]

# Code, templates, markup and tool or prompt-control instructions.
_CODE_AND_TOOLS = [
    # SQL function-call syntax: the model describes aggregations with the "aggregation" field instead.
    r"\b(sum|count|avg|min|max|coalesce|cast|concat|substring|date_trunc|extract|round|nullif)\s*\(",
    r"__\w+__",
    r"\b(eval|exec|compile|getattr|setattr)\s*\(",
    r"\bimport\s+[a-z_][\w.]*",
    r"\bos\.(system|popen|environ)\b",
    r"\bsubprocess\b",
    r"\blambda\b[^:\n]{0,40}:",
    r"\{\{|\}\}|\$\{|<%|%>",
    r"<\s*/?\s*(script|iframe|img|svg|object|tool|system|assistant|function)\b",
    r"\bjavascript\s*:",
    r"\b(tool_calls?|function_call)\b",
    r"\bignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions|rules)\b",
    r"```",
]

_SQL_RE = [re.compile(p, _FLAGS) for p in _SQL_STATEMENT]
_CODE_RE = [re.compile(p, _FLAGS) for p in _CODE_AND_TOOLS]
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def executable_content_problem(text: str) -> str | None:
    """A short reason when ``text`` contains SQL, code or tool instructions; otherwise ``None``.

    The reason never quotes the text itself.
    """
    if _CONTROL_RE.search(text):
        return "contains control characters"
    for pattern in _SQL_RE:
        if pattern.search(text):
            return "contains an SQL fragment"
    for pattern in _CODE_RE:
        if pattern.search(text):
            return "contains code or tool instructions"
    return None


def question_problem(text: str) -> str | None:
    """Why a user's question cannot be interpreted as plain language, or ``None``."""
    if _CONTROL_RE.search(text):
        return "The question contains control characters."
    for pattern in _SQL_RE:
        if pattern.search(text):
            return "Ask the question in plain language. SQL statements are not accepted."
    return None


def ensure_safe_text(value: str) -> str:
    """Pydantic ``AfterValidator``: reject executable content in model-provided text."""
    problem = executable_content_problem(value)
    if problem is not None:
        raise ValueError(f"text {problem}")
    return value
