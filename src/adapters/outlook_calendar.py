"""Outlook/365 calendar adapter — implements CalendarPort via Microsoft Graph API.

All Microsoft-specific logic lives here. Core modules never import this
directly; they depend on the CalendarPort protocol.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.event import Event
from msgraph.generated.models.patterned_recurrence import PatternedRecurrence
from msgraph.generated.models.recurrence_pattern import RecurrencePattern
from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
from msgraph.generated.models.recurrence_range import RecurrenceRange
from msgraph.generated.models.recurrence_range_type import RecurrenceRangeType
from msgraph.generated.models.attendee import Attendee
from msgraph.generated.models.attendee_type import AttendeeType
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.location import Location
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)

from src.config import settings
from src.core.parser import ParsedEvent
from src.integrations.ms_auth import get_graph_client
from src.ports.calendar_port import CalendarError

logger = logging.getLogger(__name__)


def _build_recurrence(frequency_days: int, occurrences: int) -> PatternedRecurrence:
    """Convert frequency_days + occurrences into a Graph PatternedRecurrence."""
    if frequency_days == 7:
        pattern_type = RecurrencePatternType.Weekly
        interval = 1
    elif frequency_days == 1:
        pattern_type = RecurrencePatternType.Daily
        interval = 1
    else:
        pattern_type = RecurrencePatternType.Daily
        interval = frequency_days

    pattern = RecurrencePattern(
        type=pattern_type,
        interval=interval,
    )
    recurrence_range = RecurrenceRange(
        type=RecurrenceRangeType.Numbered,
        number_of_occurrences=occurrences,
    )
    return PatternedRecurrence(pattern=pattern, range=recurrence_range)


def _normalize_event(event: Event) -> dict:
    """Normalize a Graph Event object to the standard dict format."""
    start_time = ""
    if event.start:
        start_time = event.start.date_time or ""

    end_time = ""
    if event.end:
        end_time = event.end.date_time or ""

    return {
        "id": event.id or "",
        "summary": event.subject or "(no title)",
        "start_time": start_time,
        "end_time": end_time,
        "description": event.body.content if event.body else "",
        "htmlLink": event.web_link or "",
    }


class OutlookCalendarAdapter:
    """Microsoft Outlook/365 implementation of CalendarPort."""

    async def add_event(self, parsed_event: ParsedEvent) -> dict:
        start_dt = datetime.strptime(
            f"{parsed_event.date} {parsed_event.time}", "%Y-%m-%d %H:%M"
        )
        end_dt = start_dt + timedelta(minutes=parsed_event.duration_minutes)

        attendees = None
        if parsed_event.guests:
            attendees = [
                Attendee(
                    email_address=EmailAddress(address=g),
                    type=AttendeeType.Required,
                )
                for g in parsed_event.guests
            ]

        location_obj = None
        if parsed_event.location:
            location_obj = Location(display_name=parsed_event.location)

        event = Event(
            subject=parsed_event.event,
            body=ItemBody(content=parsed_event.description, content_type=BodyType.Text),
            start=DateTimeTimeZone(
                date_time=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=settings.TIMEZONE,
            ),
            end=DateTimeTimeZone(
                date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=settings.TIMEZONE,
            ),
            attendees=attendees,
            location=location_obj,
        )

        try:
            client = get_graph_client()
            created = await client.me.events.post(event)
            logger.info(
                "Outlook event created: '%s' on %s",
                parsed_event.event,
                parsed_event.date,
            )
            return _normalize_event(created)
        except Exception as exc:
            logger.error("Outlook API error (add_event): %s", exc)
            raise CalendarError(f"Failed to create event: {exc}") from exc

    async def find_events(
        self, query: str | None = None, target_date: str | None = None
    ) -> list[dict]:
        if target_date is None:
            target_date = date.today().isoformat()

        time_min = f"{target_date}T00:00:00"
        time_max = f"{target_date}T23:59:59"

        try:
            client = get_graph_client()
            query_params = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
                start_date_time=time_min,
                end_date_time=time_max,
                orderby=["start/dateTime"],
            )
            if query:
                query_params.filter = f"contains(subject, '{query}')"

            config = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
                headers={"Prefer": f'outlook.timezone="{settings.TIMEZONE}"'},
            )

            result = await client.me.calendar_view.get(config)
            events = [_normalize_event(ev) for ev in (result.value or [])]

            logger.info(
                "Found %d event(s) for query='%s' on %s",
                len(events),
                query,
                target_date,
            )
            return events
        except Exception as exc:
            logger.error("Outlook API error (find_events): %s", exc)
            raise CalendarError(f"Failed to find events: {exc}") from exc

    async def get_daily_events(
        self, target_date: str | None = None
    ) -> list[dict]:
        return await self.find_events(target_date=target_date)

    async def delete_event(self, event_id: str) -> None:
        try:
            client = get_graph_client()
            await client.me.events.by_event_id(event_id).delete()
            logger.info("Outlook event %s deleted.", event_id)
        except Exception as exc:
            logger.error("Outlook API error (delete_event): %s", exc)
            raise CalendarError(f"Failed to delete event: {exc}") from exc

    async def update_event(
        self, event_id: str, new_date: str, new_time: str
    ) -> dict:
        try:
            client = get_graph_client()
            existing = await client.me.events.by_event_id(event_id).get()

            # Calculate duration from original event
            if existing.start and existing.end and existing.start.date_time and existing.end.date_time:
                orig_start = datetime.fromisoformat(existing.start.date_time)
                orig_end = datetime.fromisoformat(existing.end.date_time)
                duration = orig_end - orig_start
            else:
                duration = timedelta(hours=1)

            new_start_dt = datetime.strptime(
                f"{new_date} {new_time}", "%Y-%m-%d %H:%M"
            )
            new_end_dt = new_start_dt + duration

            patch_event = Event(
                start=DateTimeTimeZone(
                    date_time=new_start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    time_zone=settings.TIMEZONE,
                ),
                end=DateTimeTimeZone(
                    date_time=new_end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    time_zone=settings.TIMEZONE,
                ),
            )

            updated = await client.me.events.by_event_id(event_id).patch(patch_event)
            logger.info("Outlook event %s updated to %s %s", event_id, new_date, new_time)
            return _normalize_event(updated)
        except Exception as exc:
            logger.error("Outlook API error (update_event): %s", exc)
            raise CalendarError(f"Failed to update event: {exc}") from exc

    async def add_guests(self, event_id: str, guests: list[str]) -> dict:
        try:
            client = get_graph_client()
            existing = await client.me.events.by_event_id(event_id).get()

            current_attendees = list(existing.attendees or [])
            existing_emails = {
                a.email_address.address
                for a in current_attendees
                if a.email_address and a.email_address.address
            }
            for g in guests:
                if g not in existing_emails:
                    current_attendees.append(
                        Attendee(
                            email_address=EmailAddress(address=g),
                            type=AttendeeType.Required,
                        )
                    )

            patch_event = Event(attendees=current_attendees)
            updated = await client.me.events.by_event_id(event_id).patch(patch_event)
            logger.info("Added guests %s to Outlook event %s", guests, event_id)
            return _normalize_event(updated)
        except Exception as exc:
            logger.error("Outlook API error (add_guests): %s", exc)
            raise CalendarError(f"Failed to add guests: {exc}") from exc

    async def update_event_fields(self, event_id: str, **fields: object) -> dict:
        try:
            client = get_graph_client()
            existing = await client.me.events.by_event_id(event_id).get()

            patch_kwargs: dict = {}

            if "location" in fields:
                patch_kwargs["location"] = Location(display_name=fields["location"])
            if "description" in fields:
                patch_kwargs["body"] = ItemBody(
                    content=fields["description"], content_type=BodyType.Text,
                )
            if "time" in fields:
                if existing.start and existing.end and existing.start.date_time and existing.end.date_time:
                    orig_start = datetime.fromisoformat(existing.start.date_time)
                    orig_end = datetime.fromisoformat(existing.end.date_time)
                    duration = orig_end - orig_start
                else:
                    orig_start = datetime.now()
                    duration = timedelta(hours=1)
                new_date = orig_start.strftime("%Y-%m-%d")
                new_start = datetime.strptime(f"{new_date} {fields['time']}", "%Y-%m-%d %H:%M")
                new_end = new_start + duration
                patch_kwargs["start"] = DateTimeTimeZone(
                    date_time=new_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    time_zone=settings.TIMEZONE,
                )
                patch_kwargs["end"] = DateTimeTimeZone(
                    date_time=new_end.strftime("%Y-%m-%dT%H:%M:%S"),
                    time_zone=settings.TIMEZONE,
                )
            if "add_guests" in fields or "remove_guests" in fields:
                current_attendees = list(existing.attendees or [])
                existing_emails = {
                    a.email_address.address
                    for a in current_attendees
                    if a.email_address and a.email_address.address
                }
                if "remove_guests" in fields:
                    remove_set = set(fields["remove_guests"])
                    current_attendees = [
                        a for a in current_attendees
                        if not (a.email_address and a.email_address.address in remove_set)
                    ]
                    existing_emails -= remove_set
                if "add_guests" in fields:
                    for g in fields["add_guests"]:
                        if g not in existing_emails:
                            current_attendees.append(
                                Attendee(
                                    email_address=EmailAddress(address=g),
                                    type=AttendeeType.Required,
                                )
                            )
                patch_kwargs["attendees"] = current_attendees

            patch_event = Event(**patch_kwargs)
            updated = await client.me.events.by_event_id(event_id).patch(patch_event)
            logger.info("Updated fields %s on Outlook event %s", list(fields.keys()), event_id)
            return _normalize_event(updated)
        except Exception as exc:
            logger.error("Outlook API error (update_event_fields): %s", exc)
            raise CalendarError(f"Failed to update event fields: {exc}") from exc

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

        recurrence = _build_recurrence(frequency_days, occurrences)
        # Graph requires a start date on the recurrence range
        recurrence.range.start_date = start_dt.date()

        event = Event(
            subject=summary,
            body=ItemBody(content=description, content_type=BodyType.Text),
            start=DateTimeTimeZone(
                date_time=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=settings.TIMEZONE,
            ),
            end=DateTimeTimeZone(
                date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=settings.TIMEZONE,
            ),
            recurrence=recurrence,
        )

        try:
            client = get_graph_client()
            created = await client.me.events.post(event)
            logger.info(
                "Outlook recurring event created: '%s' starting %s %s–%s, "
                "every %d days, %d occurrences",
                summary,
                start_date,
                start_time,
                end_time,
                frequency_days,
                occurrences,
            )
            return _normalize_event(created)
        except Exception as exc:
            logger.error("Outlook API error (add_recurring_event): %s", exc)
            raise CalendarError(f"Failed to create recurring event: {exc}") from exc
