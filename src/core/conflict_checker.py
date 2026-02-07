"""
LifeOS Assistant — Event Conflict Checker.

Detects time conflicts before creating or rescheduling calendar events,
and suggests nearby free slots as alternatives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.core.chore_scheduler import overlaps_any, time_str_to_minutes

if TYPE_CHECKING:
    from src.ports.calendar_port import CalendarPort

logger = logging.getLogger(__name__)


@dataclass
class ConflictResult:
    """Result of a conflict check against calendar events."""

    has_conflict: bool
    conflicting_events: list[dict] = field(default_factory=list)
    suggested_time: str | None = None


def extract_event_duration_minutes(event: dict) -> int:
    """Compute duration from an event's start_time/end_time; default 60 min."""
    start = time_str_to_minutes(event.get("start_time", ""))
    end = time_str_to_minutes(event.get("end_time", ""))
    if start is not None and end is not None and end > start:
        return end - start
    return 60


def find_nearest_free_slot(
    busy_intervals: list[tuple[int, int]],
    duration_minutes: int,
    requested_start: int,
    day_start: int = 420,
    day_end: int = 1320,
) -> str | None:
    """Find the nearest free slot of the given duration.

    Searches forward from requested_start in 15-min steps, then backward
    to day_start. Returns "HH:MM" or None if no slot fits.
    """
    sorted_busy = sorted(busy_intervals)

    def _fits(candidate: int) -> bool:
        candidate_end = candidate + duration_minutes
        if candidate < day_start or candidate_end > day_end:
            return False
        return not overlaps_any(candidate, candidate_end, sorted_busy)

    # Search forward
    t = requested_start
    while t + duration_minutes <= day_end:
        if _fits(t):
            if t != requested_start:
                return f"{t // 60:02d}:{t % 60:02d}"
            # Skip the requested time itself — we already know it conflicts
        t += 15

    # Search backward
    t = requested_start - 15
    while t >= day_start:
        if _fits(t):
            return f"{t // 60:02d}:{t % 60:02d}"
        t -= 15

    return None


async def check_conflict(
    calendar: CalendarPort,
    target_date: str,
    start_time: str,
    duration_minutes: int,
    exclude_event_id: str | None = None,
) -> ConflictResult:
    """Check whether a proposed event conflicts with existing calendar events.

    Args:
        calendar: Calendar port for fetching events.
        target_date: ISO date string (YYYY-MM-DD).
        start_time: Proposed start time (HH:MM).
        duration_minutes: Duration of the proposed event.
        exclude_event_id: Event ID to skip (for reschedule self-exclusion).

    Returns:
        ConflictResult with conflict info and optional suggested alternative.
    """
    try:
        events = await calendar.find_events(target_date=target_date)
    except Exception as exc:
        logger.error("Failed to fetch events for conflict check on %s: %s", target_date, exc)
        return ConflictResult(has_conflict=False)

    # Filter out the excluded event (self-exclusion for reschedule)
    if exclude_event_id:
        events = [ev for ev in events if ev.get("id") != exclude_event_id]

    # Build busy intervals, skipping all-day events (no start_time/end_time)
    busy_intervals: list[tuple[int, int]] = []
    for ev in events:
        st = time_str_to_minutes(ev.get("start_time", ""))
        et = time_str_to_minutes(ev.get("end_time", ""))
        if st is not None and et is not None:
            busy_intervals.append((st, et))

    req_start = time_str_to_minutes(start_time)
    if req_start is None:
        logger.warning("Invalid start_time for conflict check: %s", start_time)
        return ConflictResult(has_conflict=False)

    req_end = req_start + duration_minutes

    if not overlaps_any(req_start, req_end, busy_intervals):
        return ConflictResult(has_conflict=False)

    # Identify which events conflict
    conflicting = []
    for ev in events:
        st = time_str_to_minutes(ev.get("start_time", ""))
        et = time_str_to_minutes(ev.get("end_time", ""))
        if st is not None and et is not None:
            if req_start < et and req_end > st:
                conflicting.append(ev)

    suggested = find_nearest_free_slot(
        busy_intervals, duration_minutes, req_start,
    )

    return ConflictResult(
        has_conflict=True,
        conflicting_events=conflicting,
        suggested_time=suggested,
    )
