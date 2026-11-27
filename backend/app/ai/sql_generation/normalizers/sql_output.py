"""Normalising generated SQL and the model's reply.

* ``extract_reply_json``: the reply must be one JSON object (Module 09's strict
  extractor, reused: optional single Markdown fence, nothing else).
* ``render_sql``: SQLGlot's rendering of the parsed statement in the *same*
  dialect (pretty-printed, no trailing semicolon). This is the SQL that is
  stored, shown and fingerprinted, so what the security guard approves is
  exactly what the user saw.
* ``sql_fingerprint``: SHA-256 of that text, the identity Module 11 must match.
"""

import hashlib

from sqlglot import exp

from app.ai.understanding.normalizers.json_output import MalformedOutputError, extract_json_object


def extract_reply_json(content: str) -> str:
    return extract_json_object(content)


def render_sql(statement: exp.Expression, dialect: str) -> str:
    return statement.sql(dialect=dialect, pretty=True)


def sql_fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


__all__ = ["MalformedOutputError", "extract_reply_json", "render_sql", "sql_fingerprint"]
