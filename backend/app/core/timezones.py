"""Business timezone helpers (Module 09).

Relative dates ("today", "last month") are resolved in the installation's
business timezone, never in the server's local timezone. The effective
timezone is, in order:

1. the application setting ``business.timezone`` (changed by administrators
   with ``settings:manage`` through ``PUT /api/v1/settings/business.timezone``);
2. the ``BUSINESS_TIMEZONE`` environment variable;
3. the documented default, ``UTC``.

An invalid stored setting is ignored (with a warning) rather than breaking
question understanding.
"""

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BUSINESS_TIMEZONE = "UTC"
BUSINESS_TIMEZONE_SETTING_KEY = "business.timezone"

# IANA names: "UTC", "Asia/Kolkata", "America/Argentina/Buenos_Aires", "Etc/GMT+5".
_TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")


def timezone_problem(name: object) -> str | None:
    """Why ``name`` is not a usable IANA timezone name, or ``None`` if it is."""
    if not isinstance(name, str) or not name or len(name) > 64 or not _TZ_NAME_RE.fullmatch(name):
        return "The timezone must be an IANA timezone name such as 'UTC' or 'Asia/Kolkata'."
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"'{name}' is not a known IANA timezone."
    return None


@dataclass(frozen=True, slots=True)
class BusinessTimezone:
    name: str
    # "setting" (application setting), "environment" (BUSINESS_TIMEZONE) or "default".
    source: str

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.name)
