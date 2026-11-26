"""Pure validation of connector configuration values (no database, no network).

Used by the request schemas (``app.schemas.connector``) so malformed settings
never reach the service, and again by the execution adapter before a request
is built. Messages never echo secret values.
"""

import re

from app.core.logging import is_sensitive_key

PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# RFC 3986 path characters. Percent-encoded sequences are allowed as written.
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,511}$")
_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")

MAX_HEADER_VALUE_LENGTH = 4096
MAX_CUSTOM_HEADERS = 20
MAX_EXTRA_QUERY_PARAMS = 10
MAX_QUERY_PARAM_VALUE_LENGTH = 256

# Headers the HTTP client controls, or that would change how the request is routed or framed.
RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "upgrade",
        "te",
        "trailer",
        "expect",
        "proxy-authorization",
        "proxy-connection",
        "accept-encoding",
        "cookie",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "x-http-method-override",
        "x-http-method",
        "x-method-override",
    }
)


def validate_path(value: str, *, label: str = "The endpoint path") -> str:
    path = value.strip()
    if not path.startswith("/"):
        path = "/" + path
    if not _PATH_RE.match(path) or _PERCENT_RE.search(path):
        raise ValueError(f"{label} may contain only URL path characters and must not include a query string or fragment.")
    segments = path.split("/")
    if "//" in path or any(s in (".", "..") for s in segments):
        raise ValueError(f"{label} must not contain empty, '.' or '..' segments.")
    lowered = path.lower()
    if "%2f" in lowered or "%5c" in lowered or "%2e" in lowered:
        raise ValueError(f"{label} must not contain encoded slashes or dots.")
    return path


def validate_parameter_name(value: str, *, label: str = "The parameter name") -> str:
    name = value.strip()
    if not PARAMETER_NAME_RE.match(name):
        raise ValueError(f"{label} must start with a letter or underscore and use letters, digits, '_', '-' or '.' (at most 64).")
    return name


def validate_header_name(value: str) -> str:
    name = value.strip()
    if not HEADER_NAME_RE.match(name):
        raise ValueError("Header names may contain only letters, digits and !#$%&'*+.^_`|~- (at most 64).")
    if name.lower() in RESERVED_HEADERS or name.lower().startswith(("proxy-", "sec-")):
        raise ValueError(f"The header '{name}' is managed by OpsPilot and cannot be configured.")
    return name


def validate_header_value(value: str) -> str:
    """Visible ASCII and spaces only: rules out CR/LF header injection. The value is never echoed."""
    if len(value) > MAX_HEADER_VALUE_LENGTH:
        raise ValueError(f"Header values must be at most {MAX_HEADER_VALUE_LENGTH} characters.")
    if not value.strip():
        raise ValueError("Header values must not be empty.")
    if any(not (32 <= ord(c) <= 126) and c != "\t" for c in value):
        raise ValueError("Header values may contain only printable ASCII characters (no line breaks).")
    return value


def validate_secret(value: str) -> str:
    if len(value) > MAX_HEADER_VALUE_LENGTH:
        raise ValueError(f"Credentials must be at most {MAX_HEADER_VALUE_LENGTH} characters.")
    if not value.strip():
        raise ValueError("Credentials must not be empty.")
    if any(not (33 <= ord(c) <= 126) for c in value):
        raise ValueError("Credentials may contain only printable ASCII characters without spaces or line breaks.")
    return value


def validate_query_param(name: str, value: str, *, sql_parameter_name: str) -> tuple[str, str]:
    """An additional, explicitly configured, non-sensitive query parameter."""
    name = validate_parameter_name(name, label="Query parameter names")
    if name.lower() == sql_parameter_name.lower():
        raise ValueError("An additional query parameter cannot use the SQL parameter's name.")
    if is_sensitive_key(name) or name.lower() in {"key", "auth", "sig", "signature", "access", "session"}:
        raise ValueError(
            f"The query parameter '{name}' looks like a credential. Credentials are never sent in URLs: "
            "configure authentication instead."
        )
    if len(value) > MAX_QUERY_PARAM_VALUE_LENGTH or any(not (32 <= ord(c) <= 126) for c in value):
        raise ValueError(f"Query parameter values must be printable ASCII, at most {MAX_QUERY_PARAM_VALUE_LENGTH} characters.")
    return name, value


def validate_field_name(value: str, *, label: str) -> str:
    name = value.strip()
    if not FIELD_NAME_RE.match(name):
        raise ValueError(f"{label} must be a plain JSON field name (letters, digits and underscores).")
    return name


def normalize_base_url(value: str) -> str:
    """Lower-case scheme and host, no trailing slash. Full checks happen in ``DestinationPolicy``."""
    text = value.strip()
    scheme, sep, rest = text.partition("://")
    if not sep:
        raise ValueError("The base URL must start with https://.")
    authority, slash, path = rest.partition("/")
    path = ("/" + path).rstrip("/") if slash else ""
    if path:
        path = validate_path(path, label="The base URL's path")
    return f"{scheme.lower()}://{authority.lower()}{path}"


def join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")
