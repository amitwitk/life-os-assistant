"""Tests for the Outlook/365 calendar adapter.

All Microsoft Graph API calls are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.adapters.outlook_calendar import (
    OutlookCalendarAdapter,
    _build_recurrence,
    _normalize_event,
)
from src.ports.calendar_port import CalendarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_graph_event(
    event_id="evt1",
    subject="Test Event",
    start_dt="2026-02-14T10:00:00",
    end_dt="2026-02-14T11:00:00",
    description="A test event",
    web_link="https://outlook.office.com/evt1",
):
    """Create a mock Graph Event object."""
    event = MagicMock()
    event.id = event_id
    event.subject = subject
    event.start = MagicMock()
    event.start.date_time = start_dt
    event.end = MagicMock()
    event.end.date_time = end_dt
    event.body = MagicMock()
    event.body.content = description
    event.web_link = web_link
    return event


_PATCH_CLIENT = "src.integrations.ms_auth.get_graph_client"
_PATCH_SETTINGS = "src.adapters.outlook_calendar.settings"


# ---------------------------------------------------------------------------
# Tests for _build_recurrence
# ---------------------------------------------------------------------------


class TestBuildRecurrence:
    def test_weekly_recurrence(self):
        rec = _build_recurrence(frequency_days=7, occurrences=4)
        from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
        assert rec.pattern.type == RecurrencePatternType.Weekly
        assert rec.pattern.interval == 1
        assert rec.range.number_of_occurrences == 4

    def test_daily_recurrence(self):
        rec = _build_recurrence(frequency_days=1, occurrences=7)
        from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
        assert rec.pattern.type == RecurrencePatternType.Daily
        assert rec.pattern.interval == 1
        assert rec.range.number_of_occurrences == 7

    def test_interval_recurrence(self):
        rec = _build_recurrence(frequency_days=3, occurrences=10)
        from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
        assert rec.pattern.type == RecurrencePatternType.Daily
        assert rec.pattern.interval == 3
        assert rec.range.number_of_occurrences == 10


# ---------------------------------------------------------------------------
# Tests for _normalize_event
# ---------------------------------------------------------------------------


class TestNormalizeEvent:
    def test_normalizes_full_event(self):
        mock_ev = _mock_graph_event()
        result = _normalize_event(mock_ev)
        assert result["id"] == "evt1"
        assert result["summary"] == "Test Event"
        assert result["start_time"] == "2026-02-14T10:00:00"
        assert result["end_time"] == "2026-02-14T11:00:00"
        assert result["description"] == "A test event"
        assert result["htmlLink"] == "https://outlook.office.com/evt1"

    def test_normalizes_empty_event(self):
        event = MagicMock()
        event.id = None
        event.subject = None
        event.start = None
        event.end = None
        event.body = None
        event.web_link = None
        result = _normalize_event(event)
        assert result["id"] == ""
        assert result["summary"] == "(no title)"
        assert result["start_time"] == ""
        assert result["end_time"] == ""


# ---------------------------------------------------------------------------
# Tests for OutlookCalendarAdapter
# ---------------------------------------------------------------------------


class TestOutlookAddEvent:
    @pytest.mark.asyncio
    async def test_add_event_success(self):
        mock_client = MagicMock()
        created_event = _mock_graph_event()
        mock_client.me.events.post = AsyncMock(return_value=created_event)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            result = await adapter.add_event(parsed)

        assert result["id"] == "evt1"
        assert result["summary"] == "Test Event"

    @pytest.mark.asyncio
    async def test_add_event_api_failure(self):
        mock_client = MagicMock()
        mock_client.me.events.post = AsyncMock(side_effect=Exception("API down"))

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            with pytest.raises(CalendarError):
                await adapter.add_event(parsed)


class TestOutlookFindEvents:
    @pytest.mark.asyncio
    async def test_find_events_success(self):
        mock_client = MagicMock()
        result_obj = MagicMock()
        result_obj.value = [_mock_graph_event()]
        mock_client.me.calendar_view.get = AsyncMock(return_value=result_obj)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            events = await adapter.find_events(target_date="2026-02-14")

        assert len(events) == 1
        assert events[0]["summary"] == "Test Event"

    @pytest.mark.asyncio
    async def test_find_events_empty(self):
        mock_client = MagicMock()
        result_obj = MagicMock()
        result_obj.value = []
        mock_client.me.calendar_view.get = AsyncMock(return_value=result_obj)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            events = await adapter.find_events(target_date="2026-02-14")

        assert events == []

    @pytest.mark.asyncio
    async def test_find_events_api_failure(self):
        mock_client = MagicMock()
        mock_client.me.calendar_view.get = AsyncMock(side_effect=Exception("fail"))

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.find_events(target_date="2026-02-14")


class TestOutlookDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_success(self):
        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.delete = AsyncMock()

        with patch(_PATCH_CLIENT, return_value=mock_client):
            adapter = OutlookCalendarAdapter()
            await adapter.delete_event("evt1")

        mock_client.me.events.by_event_id.assert_called_once_with("evt1")

    @pytest.mark.asyncio
    async def test_delete_failure(self):
        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.delete = AsyncMock(
            side_effect=Exception("fail")
        )

        with patch(_PATCH_CLIENT, return_value=mock_client):
            adapter = OutlookCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.delete_event("evt1")


class TestOutlookUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_success(self):
        existing = _mock_graph_event(
            start_dt="2026-02-14T10:00:00",
            end_dt="2026-02-14T11:00:00",
        )
        updated = _mock_graph_event(
            start_dt="2026-02-15T14:00:00",
            end_dt="2026-02-15T15:00:00",
        )

        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.get = AsyncMock(return_value=existing)
        mock_client.me.events.by_event_id.return_value.patch = AsyncMock(return_value=updated)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            result = await adapter.update_event("evt1", "2026-02-15", "14:00")

        assert result["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_update_failure(self):
        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.get = AsyncMock(
            side_effect=Exception("fail")
        )

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.update_event("evt1", "2026-02-15", "14:00")


class TestOutlookAddRecurringEvent:
    @pytest.mark.asyncio
    async def test_recurring_event_success(self):
        created = _mock_graph_event(event_id="rec1")
        mock_client = MagicMock()
        mock_client.me.events.post = AsyncMock(return_value=created)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            result = await adapter.add_recurring_event(
                summary="Chore",
                description="Test",
                start_date="2026-02-08",
                start_time="17:00",
                end_time="17:30",
                frequency_days=7,
                occurrences=4,
            )

        assert result["id"] == "rec1"
        # Verify post was called with an Event that has recurrence
        call_arg = mock_client.me.events.post.call_args[0][0]
        assert call_arg.recurrence is not None

    @pytest.mark.asyncio
    async def test_recurring_event_failure(self):
        mock_client = MagicMock()
        mock_client.me.events.post = AsyncMock(side_effect=Exception("fail"))

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.add_recurring_event(
                    summary="Fail",
                    description="",
                    start_date="2026-02-08",
                    start_time="10:00",
                    end_time="10:30",
                    frequency_days=7,
                    occurrences=4,
                )


class TestOutlookAddEventWithGuests:
    @pytest.mark.asyncio
    async def test_add_event_with_guests(self):
        mock_client = MagicMock()
        created_event = _mock_graph_event()
        mock_client.me.events.post = AsyncMock(return_value=created_event)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00", guests=["a@test.com"])
            await adapter.add_event(parsed)

        call_arg = mock_client.me.events.post.call_args[0][0]
        assert call_arg.attendees is not None
        assert len(call_arg.attendees) == 1

    @pytest.mark.asyncio
    async def test_add_event_without_guests(self):
        mock_client = MagicMock()
        created_event = _mock_graph_event()
        mock_client.me.events.post = AsyncMock(return_value=created_event)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            await adapter.add_event(parsed)

        call_arg = mock_client.me.events.post.call_args[0][0]
        assert call_arg.attendees is None


class TestOutlookAddGuests:
    @pytest.mark.asyncio
    async def test_add_guests_success(self):
        existing_event = MagicMock()
        existing_event.attendees = []
        updated_event = _mock_graph_event()

        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.get = AsyncMock(return_value=existing_event)
        mock_client.me.events.by_event_id.return_value.patch = AsyncMock(return_value=updated_event)

        with patch(_PATCH_CLIENT, return_value=mock_client):
            adapter = OutlookCalendarAdapter()
            result = await adapter.add_guests("evt1", ["new@test.com"])

        assert result["id"] == "evt1"
        patch_arg = mock_client.me.events.by_event_id.return_value.patch.call_args[0][0]
        assert patch_arg.attendees is not None
        assert len(patch_arg.attendees) == 1

    @pytest.mark.asyncio
    async def test_add_guests_failure(self):
        mock_client = MagicMock()
        mock_client.me.events.by_event_id.return_value.get = AsyncMock(
            side_effect=Exception("fail")
        )

        with patch(_PATCH_CLIENT, return_value=mock_client):
            adapter = OutlookCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.add_guests("evt1", ["a@test.com"])


class TestOutlookGetDailyEvents:
    @pytest.mark.asyncio
    async def test_delegates_to_find_events(self):
        mock_client = MagicMock()
        result_obj = MagicMock()
        result_obj.value = [_mock_graph_event()]
        mock_client.me.calendar_view.get = AsyncMock(return_value=result_obj)

        with patch(_PATCH_CLIENT, return_value=mock_client), \
             patch(_PATCH_SETTINGS) as mock_settings:
            mock_settings.TIMEZONE = "Asia/Jerusalem"
            adapter = OutlookCalendarAdapter()
            events = await adapter.get_daily_events(target_date="2026-02-14")

        assert len(events) == 1
