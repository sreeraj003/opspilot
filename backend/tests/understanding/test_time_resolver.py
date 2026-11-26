"""Deterministic time resolution: calendar and rolling periods, boundaries, timezones, leap years."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ai.understanding.normalizers.time_resolver import TimeResolutionError, TimeResolver, add_months
from app.ai.understanding.schemas.intent import ComparisonKind, TimeExpressionKind as K
from app.core.timezones import timezone_problem


def resolver(day: date, tz: str = "UTC", hour: int = 12) -> TimeResolver:
    return TimeResolver(tz, datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo(tz)))


def span(kind: K, day: date = date(2026, 3, 15), **kwargs: object) -> tuple[date, date]:
    period = resolver(day).resolve(kind, **kwargs).period  # type: ignore[arg-type]
    return period.start, period.end


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (K.TODAY, (date(2026, 3, 15), date(2026, 3, 16))),
        (K.YESTERDAY, (date(2026, 3, 14), date(2026, 3, 15))),
        # 2026-03-15 is a Sunday: ISO weeks run Monday to Sunday.
        (K.THIS_WEEK, (date(2026, 3, 9), date(2026, 3, 16))),
        (K.LAST_WEEK, (date(2026, 3, 2), date(2026, 3, 9))),
        (K.THIS_MONTH, (date(2026, 3, 1), date(2026, 4, 1))),
        (K.LAST_MONTH, (date(2026, 2, 1), date(2026, 3, 1))),
        (K.THIS_QUARTER, (date(2026, 1, 1), date(2026, 4, 1))),
        (K.LAST_QUARTER, (date(2025, 10, 1), date(2026, 1, 1))),
        (K.THIS_YEAR, (date(2026, 1, 1), date(2027, 1, 1))),
        (K.LAST_YEAR, (date(2025, 1, 1), date(2026, 1, 1))),
    ],
)
def test_calendar_periods_are_half_open(kind: K, expected: tuple[date, date]) -> None:
    assert span(kind) == expected


def test_rolling_periods_end_with_today_and_include_it() -> None:
    assert span(K.LAST_N_DAYS, quantity=7) == (date(2026, 3, 9), date(2026, 3, 16))
    assert span(K.LAST_N_DAYS, quantity=30) == (date(2026, 2, 14), date(2026, 3, 16))
    assert span(K.LAST_N_WEEKS, quantity=2) == (date(2026, 3, 2), date(2026, 3, 16))
    assert span(K.LAST_N_MONTHS, quantity=12) == (date(2025, 3, 16), date(2026, 3, 16))
    assert span(K.LAST_N_YEARS, quantity=1) == (date(2025, 3, 16), date(2026, 3, 16))
    resolved = resolver(date(2026, 3, 15)).resolve(K.LAST_N_DAYS, quantity=7)
    assert resolved.is_rolling and resolved.is_partial and resolved.period.label == "Last 7 days"
    assert (resolved.period.end - resolved.period.start) == timedelta(days=7)


def test_calendar_and_rolling_flags() -> None:
    r = resolver(date(2026, 3, 15))
    assert r.resolve(K.THIS_MONTH).is_partial and not r.resolve(K.THIS_MONTH).is_rolling
    assert not r.resolve(K.LAST_MONTH).is_partial


def test_year_transitions() -> None:
    new_year = date(2026, 1, 1)
    assert span(K.LAST_MONTH, new_year) == (date(2025, 12, 1), date(2026, 1, 1))
    assert span(K.YESTERDAY, new_year) == (date(2025, 12, 31), date(2026, 1, 1))
    assert span(K.LAST_QUARTER, new_year) == (date(2025, 10, 1), date(2026, 1, 1))
    assert span(K.LAST_WEEK, new_year) == (date(2025, 12, 22), date(2025, 12, 29))
    assert span(K.THIS_WEEK, new_year) == (date(2025, 12, 29), date(2026, 1, 5))


def test_leap_years() -> None:
    assert span(K.LAST_MONTH, date(2024, 3, 10)) == (date(2024, 2, 1), date(2024, 3, 1))
    assert span(K.CALENDAR_MONTH, date(2024, 6, 1), month=2, year=2024) == (date(2024, 2, 1), date(2024, 3, 1))
    # 29 February steps back one year to 28 February (clamped).
    assert add_months(date(2024, 2, 29), -12) == date(2023, 2, 28)
    assert span(K.LAST_N_YEARS, date(2024, 2, 28), quantity=1) == (date(2023, 2, 28), date(2024, 2, 29))
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)


def test_named_periods_and_assumed_years() -> None:
    r = resolver(date(2026, 3, 15))
    march = r.resolve(K.CALENDAR_MONTH, month=3, year=2025)
    assert (march.period.start, march.period.end, march.period.label) == (date(2025, 3, 1), date(2025, 4, 1), "March 2025")
    june = r.resolve(K.CALENDAR_MONTH, month=6)  # June has not started in 2026 yet
    assert june.period.start == date(2025, 6, 1) and "Assumed June 2025" in june.assumptions[0]
    assert r.resolve(K.CALENDAR_QUARTER, quarter=1).period.start == date(2026, 1, 1)
    assert r.resolve(K.CALENDAR_QUARTER, quarter=4).period.start == date(2025, 10, 1)
    assert r.resolve(K.CALENDAR_YEAR, year=2024).period.end == date(2025, 1, 1)


def test_explicit_range_end_is_stored_exclusive() -> None:
    start, end = span(K.EXPLICIT_RANGE, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
    assert (start, end) == (date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(TimeResolutionError):
        span(K.EXPLICIT_RANGE, start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))
    with pytest.raises(TimeResolutionError):
        span(K.LAST_N_DAYS)


def test_business_timezone_decides_today_not_the_server() -> None:
    instant = datetime(2026, 3, 31, 20, 0, tzinfo=UTC)  # 01:30 on 1 April in Kolkata
    utc = TimeResolver("UTC", instant)
    kolkata = TimeResolver("Asia/Kolkata", instant)
    assert utc.today == date(2026, 3, 31) and kolkata.today == date(2026, 4, 1)
    assert utc.resolve(K.THIS_MONTH).period.start == date(2026, 3, 1)
    assert kolkata.resolve(K.THIS_MONTH).period.start == date(2026, 4, 1)
    assert kolkata.instant(date(2026, 4, 1)).isoformat() == "2026-04-01T00:00:00+05:30"
    with pytest.raises(ValueError):
        TimeResolver("UTC", datetime(2026, 1, 1))  # naive datetimes are refused


def test_daylight_saving_offsets_are_per_boundary() -> None:
    r = TimeResolver("America/New_York", datetime(2026, 3, 20, 12, tzinfo=UTC))
    period = r.resolve(K.THIS_MONTH).period
    assert r.instant(period.start).utcoffset() == timedelta(hours=-5)
    assert r.instant(period.end).utcoffset() == timedelta(hours=-4)


def test_comparison_periods() -> None:
    r = resolver(date(2026, 3, 15))
    this_month = r.resolve(K.THIS_MONTH)
    previous = r.comparison(this_month, ComparisonKind.PREVIOUS_PERIOD)
    assert (previous.start, previous.end) == (date(2026, 2, 1), date(2026, 3, 1))
    last_year = r.comparison(this_month, ComparisonKind.SAME_PERIOD_LAST_YEAR)
    assert (last_year.start, last_year.end) == (date(2025, 3, 1), date(2025, 4, 1))
    week = r.resolve(K.LAST_N_DAYS, quantity=7)
    before = r.comparison(week, ComparisonKind.PREVIOUS_PERIOD)
    assert (before.start, before.end) == (date(2026, 3, 2), date(2026, 3, 9))
    quarter = r.comparison(r.resolve(K.LAST_QUARTER), ComparisonKind.PREVIOUS_PERIOD)
    assert (quarter.start, quarter.end) == (date(2025, 7, 1), date(2025, 10, 1))


def test_timezone_names_are_validated() -> None:
    assert timezone_problem("Asia/Kolkata") is None and timezone_problem("UTC") is None
    assert timezone_problem("Mars/Olympus") is not None
    assert timezone_problem("../etc/passwd") is not None
    assert timezone_problem(5) is not None
