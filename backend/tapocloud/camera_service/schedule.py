"""Recording schedules.

A schedule is a list of windows::

    [
      {"days": [0, 1, 2, 3, 4], "start": "22:00", "end": "08:00"},   # Mon-Fri nights
      {"days": [5, 6],          "start": "00:00", "end": "24:00"},   # whole weekend
    ]

``days`` uses Python's ``weekday()`` (0 = Monday). A window whose ``end`` is
earlier than ``start`` crosses midnight and belongs to the day it *starts*
(Friday 22:00-08:00 covers Saturday 00:00-08:00 too).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

_HHMM_ERR = "Time must be HH:MM (00:00-24:00)"


def parse_hhmm(value: str) -> time | None:
    """'22:30' -> time(22, 30). '24:00' -> None (= end of day)."""
    try:
        h, m = (int(x) for x in value.split(":"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(_HHMM_ERR) from exc
    if (h, m) == (24, 0):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(_HHMM_ERR)
    return time(h, m)


def validate(schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for w in schedule:
        days = sorted({int(d) for d in w.get("days", [])})
        if not days or any(d < 0 or d > 6 for d in days):
            raise ValueError("days must contain values 0..6")
        parse_hhmm(w["start"])
        parse_hhmm(w["end"])
        if w["start"] == w["end"]:
            raise ValueError("start and end cannot be equal (use 00:00-24:00 for the whole day)")
        clean.append({"days": days, "start": w["start"], "end": w["end"]})
    return clean


def _window_contains(window: dict[str, Any], now: datetime) -> bool:
    start = parse_hhmm(window["start"]) or time(0, 0)
    end = parse_hhmm(window["end"])  # None = 24:00
    days = set(window["days"])
    t = now.time()
    if end is None or start < end:  # same-day window
        return now.weekday() in days and t >= start and (end is None or t < end)
    # Overnight window: today's evening part, or yesterday's spill-over.
    if now.weekday() in days and t >= start:
        return True
    yesterday = (now - timedelta(days=1)).weekday()
    return yesterday in days and t < end


def is_active(schedule: list[dict[str, Any]] | None, now: datetime | None = None) -> bool:
    """True when ``now`` (local time) falls inside any window."""
    if not schedule:
        return False
    now = now or datetime.now()
    return any(_window_contains(w, now) for w in schedule)
