"""CalDAV calendar adapter — implements CalendarPort for CalDAV servers.

Supports iCloud, Nextcloud, Fastmail, and any CalDAV-compliant server.
Uses the caldav library (sync) wrapped with asyncio.to_thread for async
compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta

import caldav
from icalendar import Calendar as iCalendar
from icalendar import Event as iEvent
from icalendar import vCalAddress

from src.config import settings
from src.core.parser import ParsedEvent
from src.ports.calendar_port import CalendarError

logger = logging.getLogger(__name__)


def _get_calendar() -> caldav.Calendar:
    """Connect to CalDAV server and return the configured calendar."""
    client = caldav.DAVClient(
        url=settings.CALDAV_URL,
        username=settings.CALDAV_USERNAME,
        password=settings.CALDAV_PASSWORD,
    )
    principal = client.principal()
    calendars = principal.calendars()

    if not calendars:
        raise CalendarError("No calendars found on the CalDAV server.")

    if settings.CALDAV_CALENDAR_NAME:
        for cal in calendars:
            if cal.name == settings.CALDAV_CALENDAR_NAME:
                return cal
        raise CalendarError(
            f"Calendar '{settings.CALDAV_CALENDAR_NAME}' not found. "
            f"Available: {[c.name for c in calendars]}"
        )

    return calendars[0]


def _build_vevent(
    summary: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    uid: str | None = None,
    rrule: str | None = None,
    attendees: list[str] | None = None,
) -> str:
    """Build an iCalendar VEVENT string."""
    cal = iCalendar()
    cal.add("prodid", "-//LifeOS Assistant//EN")
    cal.add("version", "2.0")

    event = iEvent()
    event.add("uid", uid or str(uuid.uuid4()))
    event.add("summary", summary)
    event.add("description", description)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)

    if rrule:
        # Parse RRULE string like "FREQ=DAILY;COUNT=7" into dict
        parts = {}
        for part in rrule.split(";"):
            key, _, value = part.partition("=")
            parts[key] = value
        event.add("rrule", parts)

    if attendees:
        for email in attendees:
            attendee = vCalAddress(f"mailto:{email}")
            attendee.params["ROLE"] = "REQ-PARTICIPANT"
            event.add("attendee", attendee)

    cal.add_component(event)
    return cal.to_ical().decode("utf-8")


def _parse_vevent(event_data: caldav.Event) -> dict:
    """Parse a CalDAV event into the standard dict format."""
    try:
        cal = iCalendar.from_ical(event_data.data)
    except Exception:
        return {
            "id": "",
            "summary": "(parse error)",
            "start_time": "",
            "end_time": "",
            "description": "",
            "htmlLink": "",
        }

    for component in cal.walk():
        if component.name == "VEVENT":
            uid = str(component.get("uid", ""))
            summary = str(component.get("summary", "(no title)"))
            description = str(component.get("description", ""))

            dtstart = component.get("dtstart")
            dtend = component.get("dtend")

            start_time = ""
            if dtstart:
                dt = dtstart.dt
                start_time = dt.isoformat() if hasattr(dt, "hour") else dt.isoformat()

            end_time = ""
            if dtend:
                dt = dtend.dt
                end_time = dt.isoformat() if hasattr(dt, "hour") else dt.isoformat()

            return {
                "id": uid,
                "summary": summary,
                "start_time": start_time,
                "end_time": end_time,
                "description": description,
                "htmlLink": "",
            }

    return {
        "id": "",
        "summary": "(no event)",
        "start_time": "",
        "end_time": "",
        "description": "",
        "htmlLink": "",
    }


class CalDAVCalendarAdapter:
    """CalDAV implementation of CalendarPort."""

    async def add_event(self, parsed_event: ParsedEvent) -> dict:
        start_dt = datetime.strptime(
            f"{parsed_event.date} {parsed_event.time}", "%Y-%m-%d %H:%M"
        )
        end_dt = start_dt + timedelta(minutes=parsed_event.duration_minutes)

        uid = str(uuid.uuid4())
        vcal = _build_vevent(
            summary=parsed_event.event,
            description=parsed_event.description,
            start_dt=start_dt,
            end_dt=end_dt,
            uid=uid,
            attendees=parsed_event.guests or None,
        )

        try:
            cal = await asyncio.to_thread(_get_calendar)
            await asyncio.to_thread(cal.save_event, vcal)
            logger.info(
                "CalDAV event created: '%s' on %s",
                parsed_event.event,
                parsed_event.date,
            )
            return {
                "id": uid,
                "summary": parsed_event.event,
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "description": parsed_event.description,
                "htmlLink": "",
            }
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (add_event): %s", exc)
            raise CalendarError(f"Failed to create event: {exc}") from exc

    async def find_events(
        self, query: str | None = None, target_date: str | None = None
    ) -> list[dict]:
        if target_date is None:
            target_date = date.today().isoformat()

        start = datetime.fromisoformat(f"{target_date}T00:00:00")
        end = datetime.fromisoformat(f"{target_date}T23:59:59")

        try:
            cal = await asyncio.to_thread(_get_calendar)
            results = await asyncio.to_thread(
                cal.search, start=start, end=end, event=True, expand=True
            )

            events = []
            for ev in results:
                parsed = _parse_vevent(ev)
                if query and query.lower() not in parsed["summary"].lower():
                    continue
                events.append(parsed)

            logger.info(
                "Found %d CalDAV event(s) for query='%s' on %s",
                len(events),
                query,
                target_date,
            )
            return events
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (find_events): %s", exc)
            raise CalendarError(f"Failed to find events: {exc}") from exc

    async def get_daily_events(
        self, target_date: str | None = None
    ) -> list[dict]:
        return await self.find_events(target_date=target_date)

    async def delete_event(self, event_id: str) -> None:
        try:
            cal = await asyncio.to_thread(_get_calendar)

            # Search broadly and find the event by UID
            results = await asyncio.to_thread(
                cal.search, start=datetime(2000, 1, 1), end=datetime(2099, 12, 31), event=True
            )
            for ev in results:
                parsed = _parse_vevent(ev)
                if parsed["id"] == event_id:
                    await asyncio.to_thread(ev.delete)
                    logger.info("CalDAV event %s deleted.", event_id)
                    return

            raise CalendarError(f"Event with UID {event_id} not found.")
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (delete_event): %s", exc)
            raise CalendarError(f"Failed to delete event: {exc}") from exc

    async def update_event(
        self, event_id: str, new_date: str, new_time: str
    ) -> dict:
        try:
            cal = await asyncio.to_thread(_get_calendar)

            results = await asyncio.to_thread(
                cal.search, start=datetime(2000, 1, 1), end=datetime(2099, 12, 31), event=True
            )

            for ev in results:
                parsed = _parse_vevent(ev)
                if parsed["id"] != event_id:
                    continue

                # Parse the existing event to get duration
                ical = iCalendar.from_ical(ev.data)
                for component in ical.walk():
                    if component.name != "VEVENT":
                        continue

                    dtstart = component.get("dtstart")
                    dtend = component.get("dtend")
                    if dtstart and dtend:
                        duration = dtend.dt - dtstart.dt
                    else:
                        duration = timedelta(hours=1)

                    new_start = datetime.strptime(
                        f"{new_date} {new_time}", "%Y-%m-%d %H:%M"
                    )
                    new_end = new_start + duration

                    component["dtstart"].dt = new_start
                    component["dtend"].dt = new_end

                    ev.data = ical.to_ical().decode("utf-8")
                    await asyncio.to_thread(ev.save)

                    logger.info("CalDAV event %s updated to %s %s", event_id, new_date, new_time)
                    return {
                        "id": event_id,
                        "summary": str(component.get("summary", "")),
                        "start_time": new_start.isoformat(),
                        "end_time": new_end.isoformat(),
                        "description": str(component.get("description", "")),
                        "htmlLink": "",
                    }

            raise CalendarError(f"Event with UID {event_id} not found.")
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (update_event): %s", exc)
            raise CalendarError(f"Failed to update event: {exc}") from exc

    async def add_guests(self, event_id: str, guests: list[str]) -> dict:
        try:
            cal = await asyncio.to_thread(_get_calendar)
            results = await asyncio.to_thread(
                cal.search, start=datetime(2000, 1, 1), end=datetime(2099, 12, 31), event=True
            )

            for ev in results:
                parsed = _parse_vevent(ev)
                if parsed["id"] != event_id:
                    continue

                ical = iCalendar.from_ical(ev.data)
                for component in ical.walk():
                    if component.name != "VEVENT":
                        continue

                    existing_emails = set()
                    for att in component.get("attendee", []):
                        email = str(att).replace("mailto:", "")
                        existing_emails.add(email)

                    for email in guests:
                        if email not in existing_emails:
                            attendee = vCalAddress(f"mailto:{email}")
                            attendee.params["ROLE"] = "REQ-PARTICIPANT"
                            component.add("attendee", attendee)

                    ev.data = ical.to_ical().decode("utf-8")
                    await asyncio.to_thread(ev.save)
                    logger.info("Added guests %s to CalDAV event %s", guests, event_id)
                    return _parse_vevent(ev)

            raise CalendarError(f"Event with UID {event_id} not found.")
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (add_guests): %s", exc)
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

        # Build RRULE (RFC 5545 — native to iCalendar)
        if frequency_days == 1:
            rrule = f"FREQ=DAILY;COUNT={occurrences}"
        elif frequency_days == 7:
            rrule = f"FREQ=WEEKLY;COUNT={occurrences}"
        else:
            rrule = f"FREQ=DAILY;INTERVAL={frequency_days};COUNT={occurrences}"

        uid = str(uuid.uuid4())
        vcal = _build_vevent(
            summary=summary,
            description=description,
            start_dt=start_dt,
            end_dt=end_dt,
            uid=uid,
            rrule=rrule,
        )

        try:
            cal = await asyncio.to_thread(_get_calendar)
            await asyncio.to_thread(cal.save_event, vcal)
            logger.info(
                "CalDAV recurring event created: '%s' starting %s %s–%s, "
                "every %d days, %d occurrences",
                summary,
                start_date,
                start_time,
                end_time,
                frequency_days,
                occurrences,
            )
            return {
                "id": uid,
                "summary": summary,
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "description": description,
                "htmlLink": "",
            }
        except CalendarError:
            raise
        except Exception as exc:
            logger.error("CalDAV error (add_recurring_event): %s", exc)
            raise CalendarError(f"Failed to create recurring event: {exc}") from exc
