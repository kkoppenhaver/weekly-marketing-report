from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def current_week_id(today: date | None = None) -> str:
    """ISO week identifier like '2026-W18'."""
    today = today or datetime.now(timezone.utc).date()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def report_week_id(today: date | None = None) -> str:
    """The week the weekly report should cover — the previous fully-complete ISO week.

    Sunday cron runs at the end of the report week and produces a report on it.
    Using current_week_id() instead would produce misleading WoW deltas because
    GSC has a 2-day data lag, so the in-progress week is always partial.
    """
    return previous_week_id(current_week_id(today))


def previous_week_id(week_id: str) -> str:
    year, week = _parse(week_id)
    monday = date.fromisocalendar(year, week, 1)
    prev = monday - timedelta(days=7)
    py, pw, _ = prev.isocalendar()
    return f"{py}-W{pw:02d}"


def week_bounds(week_id: str) -> tuple[date, date]:
    """Inclusive Monday and Sunday for an ISO week."""
    year, week = _parse(week_id)
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _parse(week_id: str) -> tuple[int, int]:
    year_str, week_str = week_id.split("-W")
    return int(year_str), int(week_str)
