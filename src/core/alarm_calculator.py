"""Nightly alarm calculator — pure business logic.

Finds the first timed event of the day, calculates the optimal alarm time
factoring in preparation time and optional travel time.

No I/O: this module only transforms data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DEFAULT_ALARM = "08:00"


@dataclass
class AlarmRecommendation:
    """Bundled alarm recommendation data."""

    alarm_time: str            # HH:MM
    event_summary: str
    event_start: str           # HH:MM
    prep_minutes: int
    travel_minutes: int | None
    travel_text: str | None    # e.g. "30 mins (25 km)"


def _extract_time(raw: str) -> tuple[int, int]:
    """Extract (hour, minute) from an ISO datetime or HH:MM string.

    Raises ValueError on malformed input.
    """
    if "T" in raw:
        time_part = raw.split("T")[1][:5]
    else:
        time_part = raw[:5]

    if ":" not in time_part:
        raise ValueError(f"No colon in time part: {time_part!r}")

    hour, minute = map(int, time_part.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Hour/minute out of range: {hour}:{minute}")
    return hour, minute


def find_first_timed_event(events: list[dict]) -> dict | None:
    """Return the earliest timed (non-all-day) event, or None.

    All-day events have start_time without a "T" separator (just a date).
    Timed events have ISO format like "2025-01-15T09:00:00+02:00".
    """
    timed = [ev for ev in events if "T" in ev.get("start_time", "")]
    if not timed:
        return None
    timed.sort(key=lambda ev: ev["start_time"])
    return timed[0]


def calculate_alarm_time(
    event_start: str,
    prep_minutes: int,
    travel_minutes: int = 0,
) -> str:
    """Calculate alarm time as HH:MM given an event start time.

    Args:
        event_start: ISO datetime string (e.g. "2025-01-15T09:00:00+02:00")
                     or HH:MM time string.
        prep_minutes: Minutes needed for morning preparation.
        travel_minutes: Minutes needed for travel (0 if unknown).

    Returns:
        Alarm time as "HH:MM" string, or "08:00" on parse failure.
    """
    try:
        hour, minute = _extract_time(event_start)
    except (ValueError, IndexError) as exc:
        logger.warning("Failed to parse event time '%s': %s", event_start, exc)
        return _DEFAULT_ALARM

    event_dt = datetime(2000, 1, 2, hour, minute)  # arbitrary date for time math
    alarm_dt = event_dt - timedelta(minutes=prep_minutes + travel_minutes)
    return alarm_dt.strftime("%H:%M")


def build_alarm_recommendation(
    event: dict,
    prep_minutes: int,
    travel_minutes: int | None = None,
    travel_text: str | None = None,
) -> AlarmRecommendation:
    """Build a complete alarm recommendation from event data."""
    actual_travel = travel_minutes or 0
    alarm_time = calculate_alarm_time(
        event["start_time"], prep_minutes, actual_travel,
    )

    start_time = event["start_time"]
    if "T" in start_time:
        start_time = start_time.split("T")[1][:5]

    return AlarmRecommendation(
        alarm_time=alarm_time,
        event_summary=event.get("summary", "(no title)"),
        event_start=start_time,
        prep_minutes=prep_minutes,
        travel_minutes=travel_minutes,
        travel_text=travel_text,
    )


def is_late_start(event_start: str, threshold_hour: int = 12) -> bool:
    """Check if the first event starts after a threshold hour (default noon).

    Used to append a "enjoy a relaxed morning" note.
    """
    try:
        hour, _ = _extract_time(event_start)
    except (ValueError, IndexError):
        return False
    return hour >= threshold_hour
