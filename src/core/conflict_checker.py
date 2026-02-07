"""
LifeOS Assistant — Event Conflict Checker.

Detects time conflicts before creating or rescheduling calendar events,
and suggests nearby free slots as alternatives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
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


def find_free_slots(
    busy_intervals: list[tuple[int, int]],
    duration_minutes: int,
    max_slots: int = 5,
    day_start: int = 480,
    day_end: int = 1200,
    current_minutes: int | None = None,
) -> list[str]:
    """Find available time slots of the given duration within business hours.

    Scans from the effective start in 30-min steps. When max_slots is 0,
    returns ALL available slots (useful for collecting raw data).

    Args:
        busy_intervals: List of (start_min, end_min) busy periods.
        duration_minutes: Required slot duration in minutes.
        max_slots: Maximum slots to return (0 = unlimited).
        day_start: Earliest slot start in minutes from midnight (default 08:00).
        day_end: Latest slot end in minutes from midnight (default 20:00).
        current_minutes: Current time in minutes from midnight. Slots before
            this are filtered out (for same-day requests).
    """
    sorted_busy = sorted(busy_intervals)

    # Filter out past times
    effective_start = day_start
    if current_minutes is not None:
        effective_start = max(day_start, current_minutes)
        # Round up to next 30-min boundary
        remainder = effective_start % 30
        if remainder != 0:
            effective_start += 30 - remainder

    slots: list[str] = []
    t = effective_start
    while t + duration_minutes <= day_end:
        if max_slots > 0 and len(slots) >= max_slots:
            break
        if not overlaps_any(t, t + duration_minutes, sorted_busy):
            slots.append(f"{t // 60:02d}:{t % 60:02d}")
        t += 30
    return slots


def spread_slots(all_slots: list[str], max_slots: int = 5) -> list[str]:
    """Pick evenly distributed slots from a list for variety.

    Instead of returning consecutive slots (08:00, 08:30, 09:00), picks
    slots spread across the full range (e.g., 08:00, 11:00, 13:30, 16:00, 19:00).
    """
    n = len(all_slots)
    if n <= max_slots:
        return list(all_slots)
    if max_slots == 1:
        return [all_slots[n // 2]]
    indices = [round(i * (n - 1) / (max_slots - 1)) for i in range(max_slots)]
    return [all_slots[i] for i in indices]


@dataclass
class FreeSlotResult:
    """Result of a free slot search — suggested spread + all available slots."""

    suggested: list[str]      # diverse spread, max 5
    all_available: list[str]  # every free 30-min slot


async def get_free_slots(
    calendar: CalendarPort,
    target_date: str,
    duration_minutes: int,
    max_slots: int = 5,
) -> FreeSlotResult:
    """Fetch calendar events for a date and return available time slots.

    Filters out past times when target_date is today. Returns a diverse
    spread of suggested slots plus the full list of available slots for
    flexible validation.

    Args:
        calendar: Calendar port for fetching events.
        target_date: ISO date string (YYYY-MM-DD).
        duration_minutes: Required slot duration in minutes.
        max_slots: Maximum suggested slots to return.

    Returns:
        FreeSlotResult with suggested (spread) and all_available slots.
        On error, both lists are empty.
    """
    try:
        events = await calendar.find_events(target_date=target_date)
    except Exception as exc:
        logger.error("Failed to fetch events for slot suggestions on %s: %s", target_date, exc)
        return FreeSlotResult(suggested=[], all_available=[])

    busy_intervals: list[tuple[int, int]] = []
    for ev in events:
        st = time_str_to_minutes(ev.get("start_time", ""))
        et = time_str_to_minutes(ev.get("end_time", ""))
        if st is not None and et is not None:
            busy_intervals.append((st, et))

    # Filter past times when target date is today
    current_minutes = None
    if target_date == date.today().isoformat():
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

    all_slots = find_free_slots(
        busy_intervals, duration_minutes,
        max_slots=0, current_minutes=current_minutes,
    )
    suggested = spread_slots(all_slots, max_slots)

    return FreeSlotResult(suggested=suggested, all_available=all_slots)


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
