"""Tests for Google Calendar operations (adapter + backward-compat facade).

All Google API calls are mocked.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from src.adapters.google_calendar import (
    GoogleCalendarAdapter,
    _build_event_body,
)
from src.ports.calendar_port import CalendarError
from src.integrations.gcal_service import (
    add_event,
    find_events,
    delete_event,
    add_recurring_event,
)
from src.core.parser import ParsedEvent

# Patch path: get_calendar_service is imported inside _get_service()
_PATCH_GCS = "src.integrations.google_auth.get_calendar_service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_service(execute_return=None, items=None):
    """Create a mock Google Calendar service."""
    service = MagicMock()
    events_resource = MagicMock()
    service.events.return_value = events_resource

    if execute_return is not None:
        events_resource.insert.return_value.execute.return_value = execute_return
        events_resource.delete.return_value.execute.return_value = None
        events_resource.get.return_value.execute.return_value = execute_return
        events_resource.update.return_value.execute.return_value = execute_return

    if items is not None:
        events_resource.list.return_value.execute.return_value = {"items": items}

    return service


# ---------------------------------------------------------------------------
# Tests for _build_event_body
# ---------------------------------------------------------------------------


class TestBuildEventBody:
    def test_builds_correct_body(self):
        parsed = ParsedEvent(
            event="Meeting", date="2026-02-14", time="14:00",
            duration_minutes=60, description="Team sync",
        )
        body = _build_event_body(parsed)
        assert body["summary"] == "Meeting"
        assert body["description"] == "Team sync"
        assert "2026-02-14T14:00:00" in body["start"]["dateTime"]
        assert "2026-02-14T15:00:00" in body["end"]["dateTime"]

    def test_builds_body_with_guests(self):
        parsed = ParsedEvent(
            event="Meeting", date="2026-02-14", time="14:00",
            duration_minutes=60, description="", guests=["a@test.com", "b@test.com"],
        )
        body = _build_event_body(parsed)
        assert "attendees" in body
        assert len(body["attendees"]) == 2
        assert body["attendees"][0] == {"email": "a@test.com"}
        assert body["attendees"][1] == {"email": "b@test.com"}

    def test_builds_body_without_guests(self):
        parsed = ParsedEvent(
            event="Meeting", date="2026-02-14", time="14:00",
            duration_minutes=60, description="",
        )
        body = _build_event_body(parsed)
        assert "attendees" not in body

    def test_builds_body_with_location(self):
        parsed = ParsedEvent(
            event="Coffee", date="2026-02-14", time="10:00",
            duration_minutes=60, description="",
            location="Blue Bottle Coffee, 1 Ferry Building, SF",
        )
        body = _build_event_body(parsed)
        assert body["location"] == "Blue Bottle Coffee, 1 Ferry Building, SF"

    def test_builds_body_without_location(self):
        parsed = ParsedEvent(
            event="Meeting", date="2026-02-14", time="14:00",
            duration_minutes=60, description="",
        )
        body = _build_event_body(parsed)
        assert "location" not in body


# ---------------------------------------------------------------------------
# Tests for add_event
# ---------------------------------------------------------------------------


class TestAddEvent:
    @pytest.mark.asyncio
    async def test_add_event_success(self):
        mock_svc = _mock_service(execute_return={"id": "evt1", "htmlLink": "https://..."})
        with patch(_PATCH_GCS, return_value=mock_svc):
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            result = await add_event(parsed)
        assert result["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_add_event_api_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.insert.return_value.execute.side_effect = Exception("API down")
        with patch(_PATCH_GCS, return_value=mock_svc):
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            with pytest.raises(CalendarError):
                await add_event(parsed)


# ---------------------------------------------------------------------------
# Tests for find_events
# ---------------------------------------------------------------------------


class TestFindEvents:
    @pytest.mark.asyncio
    async def test_find_events_returns_simplified(self):
        items = [
            {
                "id": "e1",
                "summary": "Meeting",
                "start": {"dateTime": "2026-02-14T10:00:00+03:00"},
                "end": {"dateTime": "2026-02-14T11:00:00+03:00"},
                "description": "Sync",
            }
        ]
        mock_svc = _mock_service(items=items)
        with patch(_PATCH_GCS, return_value=mock_svc):
            events = await find_events(target_date="2026-02-14")
        assert len(events) == 1
        assert events[0]["summary"] == "Meeting"
        assert events[0]["id"] == "e1"

    @pytest.mark.asyncio
    async def test_find_events_empty(self):
        mock_svc = _mock_service(items=[])
        with patch(_PATCH_GCS, return_value=mock_svc):
            events = await find_events(target_date="2026-02-14")
        assert events == []

    @pytest.mark.asyncio
    async def test_find_events_api_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.list.return_value.execute.side_effect = Exception("fail")
        with patch(_PATCH_GCS, return_value=mock_svc):
            with pytest.raises(CalendarError):
                await find_events(target_date="2026-02-14")


# ---------------------------------------------------------------------------
# Tests for delete_event
# ---------------------------------------------------------------------------


class TestDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_event_success(self):
        mock_svc = _mock_service()
        with patch(_PATCH_GCS, return_value=mock_svc):
            await delete_event("evt1")
        mock_svc.events.return_value.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_event_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.delete.return_value.execute.side_effect = Exception("fail")
        with patch(_PATCH_GCS, return_value=mock_svc):
            with pytest.raises(CalendarError):
                await delete_event("evt1")


# ---------------------------------------------------------------------------
# Tests for add_recurring_event
# ---------------------------------------------------------------------------


class TestAddRecurringEvent:
    @pytest.mark.asyncio
    async def test_creates_weekly_rrule(self):
        mock_svc = _mock_service(execute_return={"id": "rec1", "htmlLink": "https://..."})
        with patch(_PATCH_GCS, return_value=mock_svc):
            result = await add_recurring_event(
                summary="Chore", description="Test",
                start_date="2026-02-08", start_time="17:00", end_time="17:30",
                frequency_days=7, occurrences=4,
            )
        assert result["id"] == "rec1"
        # Verify the RRULE was set correctly
        call_body = mock_svc.events.return_value.insert.call_args[1]["body"]
        assert "RRULE:FREQ=WEEKLY;COUNT=4" in call_body["recurrence"]

    @pytest.mark.asyncio
    async def test_creates_daily_rrule(self):
        mock_svc = _mock_service(execute_return={"id": "rec2"})
        with patch(_PATCH_GCS, return_value=mock_svc):
            await add_recurring_event(
                summary="Daily chore", description="",
                start_date="2026-02-08", start_time="09:00", end_time="09:30",
                frequency_days=1, occurrences=7,
            )
        call_body = mock_svc.events.return_value.insert.call_args[1]["body"]
        assert "RRULE:FREQ=DAILY;COUNT=7" in call_body["recurrence"]

    @pytest.mark.asyncio
    async def test_creates_interval_rrule(self):
        mock_svc = _mock_service(execute_return={"id": "rec3"})
        with patch(_PATCH_GCS, return_value=mock_svc):
            await add_recurring_event(
                summary="Every 3 days", description="",
                start_date="2026-02-08", start_time="10:00", end_time="10:45",
                frequency_days=3, occurrences=10,
            )
        call_body = mock_svc.events.return_value.insert.call_args[1]["body"]
        assert "RRULE:FREQ=DAILY;INTERVAL=3;COUNT=10" in call_body["recurrence"]

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.insert.return_value.execute.side_effect = Exception("fail")
        with patch(_PATCH_GCS, return_value=mock_svc):
            with pytest.raises(CalendarError):
                await add_recurring_event(
                    summary="Fail", description="",
                    start_date="2026-02-08", start_time="10:00", end_time="10:30",
                    frequency_days=7, occurrences=4,
                )


# ---------------------------------------------------------------------------
# Tests for add_guests
# ---------------------------------------------------------------------------


class TestAddGuests:
    @pytest.mark.asyncio
    async def test_add_guests_success(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1", "attendees": [{"email": "existing@test.com"}, {"email": "new@test.com"}],
        })
        with patch(_PATCH_GCS, return_value=mock_svc):
            from src.integrations.gcal_service import add_guests
            result = await add_guests("evt1", ["new@test.com"])
        assert result["id"] == "evt1"
        mock_svc.events.return_value.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_guests_deduplicates(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1", "attendees": [{"email": "a@test.com"}],
        })
        # get returns event with existing attendee
        mock_svc.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1", "attendees": [{"email": "a@test.com"}],
        }
        with patch(_PATCH_GCS, return_value=mock_svc):
            from src.integrations.gcal_service import add_guests
            await add_guests("evt1", ["a@test.com", "b@test.com"])
        call_body = mock_svc.events.return_value.update.call_args[1]["body"]
        emails = [a["email"] for a in call_body["attendees"]]
        assert emails.count("a@test.com") == 1
        assert "b@test.com" in emails

    @pytest.mark.asyncio
    async def test_add_guests_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.get.return_value.execute.side_effect = Exception("fail")
        with patch(_PATCH_GCS, return_value=mock_svc):
            from src.integrations.gcal_service import add_guests
            with pytest.raises(CalendarError):
                await add_guests("evt1", ["a@test.com"])


# ---------------------------------------------------------------------------
# Tests for update_event_fields
# ---------------------------------------------------------------------------


class TestUpdateEventFields:
    @pytest.mark.asyncio
    async def test_update_location(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1", "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:00:00"},
        })
        with patch(_PATCH_GCS, return_value=mock_svc):
            adapter = GoogleCalendarAdapter()
            result = await adapter.update_event_fields("evt1", location="Blue Bottle")
        assert result["id"] == "evt1"
        call_body = mock_svc.events.return_value.update.call_args[1]["body"]
        assert call_body["location"] == "Blue Bottle"

    @pytest.mark.asyncio
    async def test_update_description(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1", "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:00:00"},
        })
        with patch(_PATCH_GCS, return_value=mock_svc):
            adapter = GoogleCalendarAdapter()
            result = await adapter.update_event_fields("evt1", description="Updated notes")
        call_body = mock_svc.events.return_value.update.call_args[1]["body"]
        assert call_body["description"] == "Updated notes"

    @pytest.mark.asyncio
    async def test_update_add_and_remove_guests(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1",
            "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:00:00"},
            "attendees": [{"email": "a@test.com"}, {"email": "b@test.com"}],
        })
        # get returns the same event
        mock_svc.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1",
            "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:00:00"},
            "attendees": [{"email": "a@test.com"}, {"email": "b@test.com"}],
        }
        with patch(_PATCH_GCS, return_value=mock_svc):
            adapter = GoogleCalendarAdapter()
            await adapter.update_event_fields(
                "evt1", add_guests=["c@test.com"], remove_guests=["a@test.com"],
            )
        call_body = mock_svc.events.return_value.update.call_args[1]["body"]
        emails = [a["email"] for a in call_body["attendees"]]
        assert "c@test.com" in emails
        assert "a@test.com" not in emails
        assert "b@test.com" in emails

    @pytest.mark.asyncio
    async def test_update_time_preserves_duration(self):
        mock_svc = _mock_service(execute_return={
            "id": "evt1",
            "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:30:00"},
        })
        mock_svc.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1",
            "start": {"dateTime": "2026-02-08T14:00:00"},
            "end": {"dateTime": "2026-02-08T15:30:00"},
        }
        with patch(_PATCH_GCS, return_value=mock_svc):
            adapter = GoogleCalendarAdapter()
            await adapter.update_event_fields("evt1", time="16:00")
        call_body = mock_svc.events.return_value.update.call_args[1]["body"]
        # Duration was 1.5h → new end should be 17:30
        assert "16:00:00" in call_body["start"]["dateTime"]
        assert "17:30:00" in call_body["end"]["dateTime"]

    @pytest.mark.asyncio
    async def test_update_failure_raises(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.get.return_value.execute.side_effect = Exception("API fail")
        with patch(_PATCH_GCS, return_value=mock_svc):
            adapter = GoogleCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.update_event_fields("evt1", location="Fail")
