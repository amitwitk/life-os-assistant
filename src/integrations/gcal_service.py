"""
LifeOS Assistant — Google Calendar Service.

Write-side of the Capture System: takes a ParsedEvent from the LLM parser
and creates the corresponding Google Calendar event.

Read-side for the Morning Briefing: fetches today's events as structured data
that the scheduler (4.2) passes to Claude for summarization.

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
from datetime import date, datetime, timedelta

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


# ---------------------------------------------------------------------------
# Read-side: Morning Briefing
# ---------------------------------------------------------------------------


async def get_daily_events(target_date: str | None = None) -> list[dict]:
    """Fetch all calendar events for a given date.

    Args:
        target_date: Date in YYYY-MM-DD format. Defaults to today.

    Returns:
        List of simplified event dicts with keys:
        id, summary, start_time, end_time, description.

        This structured output is consumed by 4.2_Scheduler,
        which passes it to Claude for summarization.

    Raises:
        CalendarError: If the Google Calendar API call fails.
    """
    return await find_events(target_date=target_date)


async def find_events(
    query: str | None = None, target_date: str | None = None
) -> list[dict]:
    """Fetch all calendar events for a given date.

    Args:
        query: Optional text to search for in event summaries.
        target_date: Date in YYYY-MM-DD format. Defaults to today.

    Returns:
        List of simplified event dicts with keys:
        id, summary, start_time, end_time, description.

    Raises:
        CalendarError: If the Google Calendar API call fails.
    """
    if target_date is None:
        target_date = date.today().isoformat()

    # Build time range: full day in Asia/Jerusalem
    time_min = f"{target_date}T00:00:00+03:00"
    time_max = f"{target_date}T23:59:59+03:00"

    try:
        service = get_calendar_service()
        result = (
            service.events()
            .list(
                calendarId="primary",
                q=query,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = []
        for item in result.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append(
                {
                    "id": item.get("id", ""),
                    "summary": item.get("summary", "(no title)"),
                    "start_time": start.get("dateTime", start.get("date", "")),
                    "end_time": end.get("dateTime", end.get("date", "")),
                    "description": item.get("description", ""),
                }
            )

        logger.info(
            "Found %d event(s) for query='%s' on %s",
            len(events),
            query,
            target_date,
        )
        return events

    except Exception as exc:
        logger.error(
            "Failed to find events for query='%s' on %s: %s",
            query,
            target_date,
            exc,
        )
        raise CalendarError(f"Failed to find events: {exc}") from exc


async def delete_event(event_id: str) -> None:
    """Delete a Google Calendar event by its ID.

    Args:
        event_id: The ID of the event to delete.

    Raises:
        CalendarError: If the API call fails.
    """
    try:
        service = get_calendar_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        logger.info("Event with ID %s deleted successfully.", event_id)
    except Exception as exc:
        logger.error("Failed to delete event with ID %s: %s", event_id, exc)
        raise CalendarError(f"Failed to delete event: {exc}") from exc
