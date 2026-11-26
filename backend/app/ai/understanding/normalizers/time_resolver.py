"""Deterministic resolution of time expressions to date ranges.

The model only says *which* expression the user used ("last month", "last 30
days", "Q1 2025"); this module computes the dates, in the installation's
business timezone and relative to an explicit reference instant. The server's
local timezone is never used.

Conventions (documented in ARCHITECTURE.md section 10.6):

* Every range is half-open: ``start`` inclusive, ``end`` **exclusive**. "Last
  month" on 2026-03-15 is ``[2026-02-01, 2026-03-01)``.
* Weeks are ISO weeks, Monday to Sunday.
* Quarters are calendar quarters: Q1 = January to March.
* Calendar periods ("this month") are whole periods, including days that have
  not happened yet; ``is_partial`` says the period contains today.
* Rolling windows ("last 7 days", "last 12 months") end with today and include
  it: ``[tomorrow - N units, tomorrow)``. Month and year steps keep the day of
  the month, clamped to the month's last day (29 February steps to 28 February
  in a common year).
* A named month or quarter without a year means its most recent occurrence
  that has started (an assumption that is reported back).
* Comparison periods: ``previous_period`` is the period of the same kind and
  length immediately before (last month for this month, the 7 days before for
  "last 7 days"); ``same_period_last_year`` shifts both ends back 12 months.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.ai.understanding.schemas.intent import ComparisonKind, TimeExpressionKind

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class TimeResolutionError(ValueError):
    """The expression cannot be resolved to a valid range (the message is safe to show)."""


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date  # exclusive
    label: str


@dataclass(frozen=True, slots=True)
class ResolvedTime:
    kind: TimeExpressionKind
    period: Period
    is_rolling: bool
    is_partial: bool
    quantity: int | None = None
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def add_months(day: date, months: int) -> date:
    """``day`` moved by ``months`` calendar months, clamped to the target month's length."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    if not 1 <= year <= 9999:
        raise TimeResolutionError("The date is outside the supported range.")
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


class TimeResolver:
    """Resolves expressions relative to ``now`` in ``timezone``. Pure and deterministic."""

    def __init__(self, timezone: str, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        self.timezone = timezone
        self.zone = ZoneInfo(timezone)
        self.now = now.astimezone(self.zone)
        self.today = self.now.date()

    def instant(self, day: date) -> datetime:
        """Midnight at the start of ``day`` in the business timezone."""
        return datetime.combine(day, time.min, tzinfo=self.zone)

    # --- main entry point -------------------------------------------------------------------------------

    def resolve(
        self,
        kind: TimeExpressionKind,
        *,
        quantity: int | None = None,
        year: int | None = None,
        month: int | None = None,
        quarter: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ResolvedTime:
        today = self.today
        k = TimeExpressionKind
        assumptions: list[str] = []
        rolling = False
        if kind == k.TODAY:
            period = Period(today, today + timedelta(days=1), "Today")
        elif kind == k.YESTERDAY:
            period = Period(today - timedelta(days=1), today, "Yesterday")
        elif kind == k.THIS_WEEK:
            start = _week_start(today)
            period = Period(start, start + timedelta(days=7), "This week")
        elif kind == k.LAST_WEEK:
            start = _week_start(today) - timedelta(days=7)
            period = Period(start, start + timedelta(days=7), "Last week")
        elif kind == k.THIS_MONTH:
            start = _month_start(today)
            period = Period(start, add_months(start, 1), "This month")
        elif kind == k.LAST_MONTH:
            end = _month_start(today)
            period = Period(add_months(end, -1), end, "Last month")
        elif kind == k.THIS_QUARTER:
            start = _quarter_start(today)
            period = Period(start, add_months(start, 3), "This quarter")
        elif kind == k.LAST_QUARTER:
            end = _quarter_start(today)
            period = Period(add_months(end, -3), end, "Last quarter")
        elif kind == k.THIS_YEAR:
            period = Period(date(today.year, 1, 1), date(today.year + 1, 1, 1), "This year")
        elif kind == k.LAST_YEAR:
            period = Period(date(today.year - 1, 1, 1), date(today.year, 1, 1), "Last year")
        elif kind in (k.LAST_N_DAYS, k.LAST_N_WEEKS, k.LAST_N_MONTHS, k.LAST_N_YEARS):
            if quantity is None or quantity < 1:
                raise TimeResolutionError("A rolling period needs a positive number of units.")
            period = self._rolling(kind, quantity)
            rolling = True
        elif kind == k.CALENDAR_MONTH:
            if month is None:
                raise TimeResolutionError("A named month needs the month.")
            if year is None:
                year = today.year if month <= today.month else today.year - 1
                assumptions.append(f"Assumed {_MONTHS[month - 1]} {year}, the most recent {_MONTHS[month - 1]}.")
            start = date(year, month, 1)
            period = Period(start, add_months(start, 1), f"{_MONTHS[month - 1]} {year}")
        elif kind == k.CALENDAR_QUARTER:
            if quarter is None:
                raise TimeResolutionError("A named quarter needs the quarter number.")
            if year is None:
                current = (today.month - 1) // 3 + 1
                year = today.year if quarter <= current else today.year - 1
                assumptions.append(f"Assumed Q{quarter} {year}, the most recent Q{quarter}.")
            start = date(year, 3 * (quarter - 1) + 1, 1)
            period = Period(start, add_months(start, 3), f"Q{quarter} {year}")
        elif kind == k.CALENDAR_YEAR:
            if year is None:
                raise TimeResolutionError("A named year needs the year.")
            period = Period(date(year, 1, 1), date(year + 1, 1, 1), str(year))
        elif kind == k.EXPLICIT_RANGE:
            if start_date is None or end_date is None:
                raise TimeResolutionError("An explicit range needs a start and an end date.")
            if start_date > end_date:
                raise TimeResolutionError("The start date is after the end date.")
            # People say end dates inclusively ("to 31 March"); store the exclusive end.
            period = Period(start_date, end_date + timedelta(days=1), f"{start_date.isoformat()} to {end_date.isoformat()}")
        else:  # pragma: no cover - the enum is exhaustive
            raise TimeResolutionError("Unsupported time expression.")
        partial = period.start <= today < period.end
        return ResolvedTime(kind, period, rolling, partial, quantity if rolling else None, tuple(assumptions))

    def _rolling(self, kind: TimeExpressionKind, quantity: int) -> Period:
        end = self.today + timedelta(days=1)
        k = TimeExpressionKind
        if kind == k.LAST_N_DAYS:
            start, unit = end - timedelta(days=quantity), "day"
        elif kind == k.LAST_N_WEEKS:
            start, unit = end - timedelta(weeks=quantity), "week"
        elif kind == k.LAST_N_MONTHS:
            start, unit = add_months(end, -quantity), "month"
        else:
            start, unit = add_months(end, -12 * quantity), "year"
        label = f"Last {quantity} {unit}{'s' if quantity != 1 else ''}"
        return Period(start, end, label)

    # --- comparisons ------------------------------------------------------------------------------------

    def comparison(self, resolved: ResolvedTime, kind: ComparisonKind) -> Period:
        period = resolved.period
        if kind == ComparisonKind.SAME_PERIOD_LAST_YEAR:
            return Period(add_months(period.start, -12), add_months(period.end, -12), f"{period.label}, previous year")
        k = TimeExpressionKind
        calendar_months = {
            k.THIS_MONTH: 1, k.LAST_MONTH: 1, k.CALENDAR_MONTH: 1,
            k.THIS_QUARTER: 3, k.LAST_QUARTER: 3, k.CALENDAR_QUARTER: 3,
            k.THIS_YEAR: 12, k.LAST_YEAR: 12, k.CALENDAR_YEAR: 12,
        }
        if resolved.kind in calendar_months:
            months = calendar_months[resolved.kind]
            return Period(add_months(period.start, -months), period.start, f"Period before {period.label.lower()}")
        if resolved.kind in (k.LAST_N_MONTHS, k.LAST_N_YEARS) and resolved.quantity:
            months = resolved.quantity * (12 if resolved.kind == k.LAST_N_YEARS else 1)
            return Period(add_months(period.start, -months), period.start, f"Period before {period.label.lower()}")
        # Day-based periods (days, weeks, explicit ranges): the same number of days immediately before.
        length = period.end - period.start
        return Period(period.start - length, period.start, f"Period before {period.label.lower()}")
