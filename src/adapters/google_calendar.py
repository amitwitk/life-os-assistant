"""Google Calendar adapter — implements CalendarPort for Google Calendar API.

All Google-specific logic lives here. Core modules never import this directly;
they depend on the CalendarPort protocol.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from src.config import settings
from src.core.parser import ParsedEvent
from src.integrations.google_auth import get_calendar_service
from src.ports.calendar_port import CalendarError

logger = logging.getLogger(__name__)


def _build_event_body(parsed_event: ParsedEvent) -> dict:
    """Construct a Google Calendar API event body from a ParsedEvent."""
    start_dt = datetime.strptime(
        f"{parsed_event.date} {parsed_event.time}", "%Y-%m-%d %H:%M"
    )
    end_dt = start_dt + timedelta(minutes=parsed_event.duration_minutes)

    body: dict = {
        "summary": parsed_event.event,
        "description": parsed_event.description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": settings.TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": settings.TIMEZONE,
        },
    }
    if parsed_event.guests:
        body["attendees"] = [{"email": g} for g in parsed_event.guests]
    return body


class GoogleCalendarAdapter:
    """Google Calendar implementation of CalendarPort."""

    async def add_event(self, parsed_event: ParsedEvent) -> dict:
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

    async def find_events(
        self, query: str | None = None, target_date: str | None = None
    ) -> list[dict]:
        if target_date is None:
            target_date = date.today().isoformat()

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

    async def get_daily_events(
        self, target_date: str | None = None
    ) -> list[dict]:
        return await self.find_events(target_date=target_date)

    async def delete_event(self, event_id: str) -> None:
        try:
            service = get_calendar_service()
            service.events().delete(
                calendarId="primary", eventId=event_id
            ).execute()
            logger.info("Event with ID %s deleted successfully.", event_id)
        except Exception as exc:
            logger.error("Failed to delete event with ID %s: %s", event_id, exc)
            raise CalendarError(f"Failed to delete event: {exc}") from exc

    async def update_event(
        self, event_id: str, new_date: str, new_time: str
    ) -> dict:
        try:
            service = get_calendar_service()
            event = (
                service.events()
                .get(calendarId="primary", eventId=event_id)
                .execute()
            )

            original_start_dt_str = event["start"].get(
                "dateTime", event["start"].get("date")
            )
            original_end_dt_str = event["end"].get(
                "dateTime", event["end"].get("date")
            )

            if "T" in original_start_dt_str and "T" in original_end_dt_str:
                original_start_dt = datetime.fromisoformat(original_start_dt_str)
                original_end_dt = datetime.fromisoformat(original_end_dt_str)
                duration = original_end_dt - original_start_dt
            else:
                duration = timedelta(hours=1)
                logger.warning(
                    "Event %s has no specific time, defaulting duration to 1 hour.",
                    event_id,
                )

            new_start_dt = datetime.strptime(
                f"{new_date} {new_time}", "%Y-%m-%d %H:%M"
            )
            new_end_dt = new_start_dt + duration

            event["start"]["dateTime"] = new_start_dt.isoformat()
            event["end"]["dateTime"] = new_end_dt.isoformat()

            updated_event = (
                service.events()
                .update(calendarId="primary", eventId=event_id, body=event)
                .execute()
            )

            logger.info(
                "Event %s updated to '%s' on %s at %s",
                event_id,
                updated_event.get("summary", ""),
                new_date,
                new_time,
            )
            return updated_event
        except Exception as exc:
            logger.error("Failed to update event with ID %s: %s", event_id, exc)
            raise CalendarError(f"Failed to update event: {exc}") from exc

    async def add_guests(self, event_id: str, guests: list[str]) -> dict:
        try:
            service = get_calendar_service()
            event = (
                service.events()
                .get(calendarId="primary", eventId=event_id)
                .execute()
            )
            existing = event.get("attendees", [])
            existing_emails = {a["email"] for a in existing}
            for g in guests:
                if g not in existing_emails:
                    existing.append({"email": g})
            event["attendees"] = existing
            updated = (
                service.events()
                .update(calendarId="primary", eventId=event_id, body=event)
                .execute()
            )
            logger.info("Added guests %s to event %s", guests, event_id)
            return updated
        except Exception as exc:
            logger.error("Failed to add guests to event %s: %s", event_id, exc)
            raise CalendarError(f"Failed to add guests: {exc}") from exc

    async def add_recurring_event(
        self,
        summary: str,
        description: str,
        start_date: str,
        start_time: str,
        end_time: str,
        frequency_days: int,
        occurrences: int,
    ) -> dict:
        start_dt = datetime.strptime(
            f"{start_date} {start_time}", "%Y-%m-%d %H:%M"
        )
        end_dt = datetime.strptime(
            f"{start_date} {end_time}", "%Y-%m-%d %H:%M"
        )

        if frequency_days == 1:
            rrule = f"RRULE:FREQ=DAILY;COUNT={occurrences}"
        elif frequency_days == 7:
            rrule = f"RRULE:FREQ=WEEKLY;COUNT={occurrences}"
        else:
            rrule = f"RRULE:FREQ=DAILY;INTERVAL={frequency_days};COUNT={occurrences}"

        body = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": settings.TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": settings.TIMEZONE,
            },
            "recurrence": [rrule],
        }

        try:
            service = get_calendar_service()
            created = (
                service.events()
                .insert(calendarId="primary", body=body)
                .execute()
            )
            logger.info(
                "Recurring event created: '%s' starting %s %s–%s, every %d days, %d occurrences — %s",
                summary,
                start_date,
                start_time,
                end_time,
                frequency_days,
                occurrences,
                created.get("htmlLink", ""),
            )
            return created
        except Exception as exc:
            logger.error("Failed to create recurring event: %s", exc)
            raise CalendarError(
                f"Failed to create recurring event: {exc}"
            ) from exc
