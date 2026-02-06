"""
LifeOS Assistant — Smart Chore Scheduler.

Finds the best fixed time slot for a recurring chore, avoiding conflicts with
existing Google Calendar events. Returns a single time to be used in a
recurring calendar event (RRULE).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


async def find_best_slot(
    chore_name: str,
    frequency_days: int,
    duration_minutes: int,
    preferred_start: str,
    preferred_end: str,
    weeks_ahead: int,
) -> dict | None:
    """Find the best single time slot for a recurring chore.

    Checks the first several candidate dates to find a time within the
    preferred window that has the fewest conflicts, then returns that
    fixed time along with recurrence metadata.

    Args:
        chore_name: Name of the chore (for logging).
        frequency_days: How often the chore repeats (in days).
        duration_minutes: How long the chore takes.
        preferred_start: Earliest start time, e.g. "17:00".
        preferred_end: Latest end time, e.g. "21:00".
        weeks_ahead: How many weeks into the future to schedule.

    Returns:
        Dict with keys: start_date, start_time, end_time, occurrences,
        frequency_days.  Or None if no slot can be found.
    """
    from src.integrations.gcal_service import find_events

    tomorrow = date.today() + timedelta(days=1)
    end_date = tomorrow + timedelta(weeks=weeks_ahead)

    # Build candidate dates
    candidate_dates: list[date] = []
    d = tomorrow
    while d < end_date:
        candidate_dates.append(d)
        d += timedelta(days=frequency_days)

    if not candidate_dates:
        return None

    pref_start = datetime.strptime(preferred_start, "%H:%M").time()
    pref_end = datetime.strptime(preferred_end, "%H:%M").time()
    needed = duration_minutes

    window_start = pref_start.hour * 60 + pref_start.minute
    window_end = pref_end.hour * 60 + pref_end.minute

    # Build all candidate time slots (15-min increments)
    candidate_times: list[int] = []
    t = window_start
    while t + needed <= window_end:
        candidate_times.append(t)
        t += 15

    if not candidate_times:
        logger.warning("No candidate times fit in window for '%s'", chore_name)
        return None

    # Check up to 5 dates to score each candidate time
    sample_dates = candidate_dates[:5]

    # Collect busy intervals for each sample date
    all_busy: list[list[tuple[int, int]]] = []
    for cd in sample_dates:
        try:
            events = await find_events(target_date=cd.isoformat())
        except Exception as exc:
            logger.error("Failed to fetch events for %s: %s", cd, exc)
            events = []
        busy: list[tuple[int, int]] = []
        for ev in events:
            st_min = _time_str_to_minutes(ev.get("start_time", ""))
            et_min = _time_str_to_minutes(ev.get("end_time", ""))
            if st_min is not None and et_min is not None:
                busy.append((st_min, et_min))
        all_busy.append(busy)

    # Score each candidate time: count how many sample dates have NO conflict
    best_time = None
    best_score = -1
    for ct in candidate_times:
        ct_end = ct + needed
        score = sum(
            1 for busy in all_busy if not _overlaps_any(ct, ct_end, busy)
        )
        if score > best_score:
            best_score = score
            best_time = ct

    if best_time is None:
        return None

    start_hm = f"{best_time // 60:02d}:{best_time % 60:02d}"
    end_min = best_time + needed
    end_hm = f"{end_min // 60:02d}:{end_min % 60:02d}"

    result = {
        "start_date": candidate_dates[0].isoformat(),
        "start_time": start_hm,
        "end_time": end_hm,
        "occurrences": len(candidate_dates),
        "frequency_days": frequency_days,
    }

    logger.info(
        "Best slot for '%s': %s at %s–%s (%d occurrences, score %d/%d)",
        chore_name, result["start_date"], start_hm, end_hm,
        len(candidate_dates), best_score, len(sample_dates),
    )
    return result


def _time_str_to_minutes(time_str: str) -> int | None:
    """Convert an ISO datetime or HH:MM string to minutes from midnight."""
    if not time_str:
        return None
    try:
        if "T" in time_str:
            t = datetime.fromisoformat(time_str).time()
        else:
            t = datetime.strptime(time_str, "%H:%M").time()
        return t.hour * 60 + t.minute
    except (ValueError, TypeError):
        return None


def _overlaps_any(
    start: int, end: int, busy: list[tuple[int, int]]
) -> bool:
    """Check if [start, end) overlaps with any busy interval."""
    for bs, be in busy:
        if start < be and end > bs:
            return True
    return False
