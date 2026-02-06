"""Tests for src.integrations.gcal_service — Google Calendar operations.

All Google API calls are mocked.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from src.integrations.gcal_service import (
    CalendarError,
    add_event,
    find_events,
    delete_event,
    add_recurring_event,
    _build_event_body,
)
from src.core.parser import ParsedEvent


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


# ---------------------------------------------------------------------------
# Tests for add_event
# ---------------------------------------------------------------------------


class TestAddEvent:
    @pytest.mark.asyncio
    async def test_add_event_success(self):
        mock_svc = _mock_service(execute_return={"id": "evt1", "htmlLink": "https://..."})
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            result = await add_event(parsed)
        assert result["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_add_event_api_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.insert.return_value.execute.side_effect = Exception("API down")
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
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
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            events = await find_events(target_date="2026-02-14")
        assert len(events) == 1
        assert events[0]["summary"] == "Meeting"
        assert events[0]["id"] == "e1"

    @pytest.mark.asyncio
    async def test_find_events_empty(self):
        mock_svc = _mock_service(items=[])
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            events = await find_events(target_date="2026-02-14")
        assert events == []

    @pytest.mark.asyncio
    async def test_find_events_api_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.list.return_value.execute.side_effect = Exception("fail")
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            with pytest.raises(CalendarError):
                await find_events(target_date="2026-02-14")


# ---------------------------------------------------------------------------
# Tests for delete_event
# ---------------------------------------------------------------------------


class TestDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_event_success(self):
        mock_svc = _mock_service()
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            await delete_event("evt1")
        mock_svc.events.return_value.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_event_failure(self):
        mock_svc = MagicMock()
        mock_svc.events.return_value.delete.return_value.execute.side_effect = Exception("fail")
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            with pytest.raises(CalendarError):
                await delete_event("evt1")


# ---------------------------------------------------------------------------
# Tests for add_recurring_event
# ---------------------------------------------------------------------------


class TestAddRecurringEvent:
    @pytest.mark.asyncio
    async def test_creates_weekly_rrule(self):
        mock_svc = _mock_service(execute_return={"id": "rec1", "htmlLink": "https://..."})
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
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
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
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
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
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
        with patch("src.integrations.gcal_service.get_calendar_service", return_value=mock_svc):
            with pytest.raises(CalendarError):
                await add_recurring_event(
                    summary="Fail", description="",
                    start_date="2026-02-08", start_time="10:00", end_time="10:30",
                    frequency_days=7, occurrences=4,
                )
