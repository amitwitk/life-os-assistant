"""
LifeOS Assistant — Google Calendar Service.

Write-side of the Capture System: takes a ParsedEvent from the LLM parser
and creates the corresponding Google Calendar event.

ParsedEvent JSON contract (defined in 1.2, consumed here and in 3.3):
{
    "event": "Dentist appointment",
    "date": "2025-02-14",
    "time": "16:00",
    "duration_minutes": 60,
    "description": ""
}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.core.parser import ParsedEvent
from src.integrations.google_auth import get_calendar_service

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Jerusalem"


class CalendarError(Exception):
    """Raised when a Google Calendar API operation fails."""


def _build_event_body(parsed_event: ParsedEvent) -> dict:
    """Construct a Google Calendar API event body from a ParsedEvent."""
    start_dt = datetime.strptime(
        f"{parsed_event.date} {parsed_event.time}", "%Y-%m-%d %H:%M"
    )
    end_dt = start_dt + timedelta(minutes=parsed_event.duration_minutes)

    return {
        "summary": parsed_event.event,
        "description": parsed_event.description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
    }


async def add_event(parsed_event: ParsedEvent) -> dict:
    """Create a Google Calendar event from a ParsedEvent.

    Returns the created event dict (contains 'htmlLink', 'id', etc.).
    Raises CalendarError if the API call fails.
    """
    event_body = _build_event_body(parsed_event)

    try:
        service = get_calendar_service()
        created = (
            service.events()
            .insert(calendarId="primary", body=event_body)
            .execute()
        )
        logger.info(
            "Event created: '%s' on %s — %s",
            parsed_event.event,
            parsed_event.date,
            created.get("htmlLink", ""),
        )
        return created
    except Exception as exc:
        logger.error("Google Calendar API error: %s", exc)
        raise CalendarError(f"Failed to create event: {exc}") from exc
