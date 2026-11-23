"""Time helpers."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(UTC)
