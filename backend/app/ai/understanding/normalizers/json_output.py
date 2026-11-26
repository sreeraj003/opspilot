"""Extracting the JSON object from a model reply, strictly.

Accepted: the reply is a single JSON object, optionally wrapped in one Markdown
code fence (```json ... ```), with surrounding whitespace. Anything else
(prose before or after, several objects, an array, an empty reply) is
malformed. Nothing is searched for or repaired inside free text.
"""

import re

from app.ai.understanding.schemas.llm_output import MAX_OUTPUT_CHARS

_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?\s*```$", re.DOTALL)


class MalformedOutputError(ValueError):
    """The reply is not one JSON object. The message is ours, never the reply's text."""


def extract_json_object(content: str) -> str:
    text = content.strip()
    if not text:
        raise MalformedOutputError("The reply was empty.")
    if len(text) > MAX_OUTPUT_CHARS:
        raise MalformedOutputError("The reply was longer than allowed.")
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    if not (text.startswith("{") and text.endswith("}")):
        raise MalformedOutputError("The reply was not a single JSON object.")
    return text
