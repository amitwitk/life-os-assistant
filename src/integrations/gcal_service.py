"""
LifeOS Assistant — Google Calendar Service (backward-compatibility facade).

DEPRECATED: This module delegates to GoogleCalendarAdapter. New code should
depend on CalendarPort and receive an adapter via dependency injection.

Kept for backward compatibility with existing tests during transition.
"""

from __future__ import annotations

import warnings

from src.adapters.google_calendar import (
    GoogleCalendarAdapter,
    _build_event_body,
)
from src.ports.calendar_port import CalendarError
from src.core.parser import ParsedEvent

# Re-export CalendarError and _build_event_body so existing tests still work
__all__ = [
    "CalendarError",
    "_build_event_body",
    "add_event",
    "find_events",
    "get_daily_events",
    "delete_event",
    "add_recurring_event",
    "update_event",
]

_adapter = GoogleCalendarAdapter()


async def add_event(parsed_event: ParsedEvent) -> dict:
    """Deprecated: use CalendarPort.add_event instead."""
    return await _adapter.add_event(parsed_event)


async def find_events(
    query: str | None = None, target_date: str | None = None
) -> list[dict]:
    """Deprecated: use CalendarPort.find_events instead."""
    return await _adapter.find_events(query=query, target_date=target_date)


async def get_daily_events(target_date: str | None = None) -> list[dict]:
    """Deprecated: use CalendarPort.get_daily_events instead."""
    return await _adapter.get_daily_events(target_date=target_date)


async def delete_event(event_id: str) -> None:
    """Deprecated: use CalendarPort.delete_event instead."""
    return await _adapter.delete_event(event_id)


async def update_event(event_id: str, new_date: str, new_time: str) -> dict:
    """Deprecated: use CalendarPort.update_event instead."""
    return await _adapter.update_event(event_id, new_date, new_time)


async def add_recurring_event(
    summary: str,
    description: str,
    start_date: str,
    start_time: str,
    end_time: str,
    frequency_days: int,
    occurrences: int,
) -> dict:
    """Deprecated: use CalendarPort.add_recurring_event instead."""
    return await _adapter.add_recurring_event(
        summary=summary,
        description=description,
        start_date=start_date,
        start_time=start_time,
        end_time=end_time,
        frequency_days=frequency_days,
        occurrences=occurrences,
    )
