"""Parse untrusted JSON within size and nesting limits.

The body is read with a byte limit before parsing, and the parsed document's
nesting depth is checked before any model sees it, so a hostile import cannot
exhaust memory or recursion.
"""

import json
from typing import Any


class JsonLimitError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def json_depth(value: Any) -> int:
    """Nesting depth of a parsed JSON value (scalars are 0), computed without recursion."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            depth += 1
            stack.extend((child, depth) for child in item.values())
        elif isinstance(item, list):
            depth += 1
            stack.extend((child, depth) for child in item)
        deepest = max(deepest, depth)
    return deepest


def parse_json(body: bytes, *, max_bytes: int, max_depth: int) -> Any:
    if len(body) > max_bytes:
        raise JsonLimitError("PAYLOAD_TOO_LARGE", f"The document is larger than {max_bytes} bytes.")
    if not body.strip():
        raise JsonLimitError("INVALID_JSON", "The request body is empty.")
    try:
        value = json.loads(body)
    except RecursionError:
        raise JsonLimitError("JSON_TOO_DEEP", f"The document is nested more than {max_depth} levels deep.") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        position = f" (line {exc.lineno}, column {exc.colno})" if isinstance(exc, json.JSONDecodeError) else ""
        raise JsonLimitError("INVALID_JSON", f"The document is not valid JSON{position}.") from None
    if json_depth(value) > max_depth:
        raise JsonLimitError("JSON_TOO_DEEP", f"The document is nested more than {max_depth} levels deep.")
    return value
