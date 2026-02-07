"""Tests for the CalDAV calendar adapter.

All CalDAV client calls are mocked.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from src.adapters.caldav_calendar import (
    CalDAVCalendarAdapter,
    _build_vevent,
    _parse_vevent,
)
from src.ports.calendar_port import CalendarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH_GET_CAL = "src.adapters.caldav_calendar._get_calendar"
_PATCH_SETTINGS = "src.adapters.caldav_calendar.settings"


def _make_caldav_event(uid="test-uid-123", summary="Test Event",
                       start_dt=None, end_dt=None, description="A test"):
    """Create a mock caldav.Event with realistic iCalendar data."""
    if start_dt is None:
        start_dt = datetime(2026, 2, 14, 10, 0)
    if end_dt is None:
        end_dt = datetime(2026, 2, 14, 11, 0)

    ical_str = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    ev = MagicMock()
    ev.data = ical_str
    ev.delete = MagicMock()
    ev.save = MagicMock()
    return ev


# ---------------------------------------------------------------------------
# Tests for _build_vevent
# ---------------------------------------------------------------------------


class TestBuildVevent:
    def test_builds_basic_vevent(self):
        result = _build_vevent(
            summary="Meeting",
            description="Team sync",
            start_dt=datetime(2026, 2, 14, 10, 0),
            end_dt=datetime(2026, 2, 14, 11, 0),
            uid="test-uid",
        )
        assert "SUMMARY:Meeting" in result
        assert "DESCRIPTION:Team sync" in result
        assert "UID:test-uid" in result
        assert "BEGIN:VEVENT" in result
        assert "END:VEVENT" in result

    def test_builds_vevent_with_rrule(self):
        result = _build_vevent(
            summary="Chore",
            description="",
            start_dt=datetime(2026, 2, 8, 17, 0),
            end_dt=datetime(2026, 2, 8, 17, 30),
            rrule="FREQ=WEEKLY;COUNT=4",
        )
        assert "RRULE:" in result
        assert "FREQ=WEEKLY" in result

    def test_daily_rrule(self):
        result = _build_vevent(
            summary="Daily",
            description="",
            start_dt=datetime(2026, 2, 8, 9, 0),
            end_dt=datetime(2026, 2, 8, 9, 30),
            rrule="FREQ=DAILY;COUNT=7",
        )
        assert "FREQ=DAILY" in result

    def test_interval_rrule(self):
        result = _build_vevent(
            summary="Every 3 days",
            description="",
            start_dt=datetime(2026, 2, 8, 10, 0),
            end_dt=datetime(2026, 2, 8, 10, 45),
            rrule="FREQ=DAILY;INTERVAL=3;COUNT=10",
        )
        assert "INTERVAL" in result

    def test_builds_vevent_with_attendees(self):
        result = _build_vevent(
            summary="Meeting",
            description="",
            start_dt=datetime(2026, 2, 14, 10, 0),
            end_dt=datetime(2026, 2, 14, 11, 0),
            attendees=["a@test.com", "b@test.com"],
        )
        assert "ATTENDEE" in result
        assert "mailto:a@test.com" in result
        assert "mailto:b@test.com" in result

    def test_builds_vevent_without_attendees(self):
        result = _build_vevent(
            summary="Meeting",
            description="",
            start_dt=datetime(2026, 2, 14, 10, 0),
            end_dt=datetime(2026, 2, 14, 11, 0),
        )
        assert "ATTENDEE" not in result

    def test_builds_vevent_with_location(self):
        result = _build_vevent(
            summary="Meeting",
            description="",
            start_dt=datetime(2026, 2, 14, 10, 0),
            end_dt=datetime(2026, 2, 14, 11, 0),
            location="Blue Bottle Coffee, 315 Linden St",
        )
        assert "LOCATION:Blue Bottle Coffee" in result

    def test_builds_vevent_without_location(self):
        result = _build_vevent(
            summary="Meeting",
            description="",
            start_dt=datetime(2026, 2, 14, 10, 0),
            end_dt=datetime(2026, 2, 14, 11, 0),
        )
        assert "LOCATION" not in result


# ---------------------------------------------------------------------------
# Tests for _parse_vevent
# ---------------------------------------------------------------------------


class TestParseVevent:
    def test_parses_basic_event(self):
        ev = _make_caldav_event()
        result = _parse_vevent(ev)
        assert result["id"] == "test-uid-123"
        assert result["summary"] == "Test Event"
        assert result["description"] == "A test"
        assert "2026" in result["start_time"]
        assert result["htmlLink"] == ""

    def test_handles_invalid_ical(self):
        ev = MagicMock()
        ev.data = "not valid ical data"
        result = _parse_vevent(ev)
        assert result["summary"] == "(parse error)"


# ---------------------------------------------------------------------------
# Tests for CalDAVCalendarAdapter
# ---------------------------------------------------------------------------


class TestCalDAVAddEvent:
    @pytest.mark.asyncio
    async def test_add_event_success(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            result = await adapter.add_event(parsed)

        assert result["summary"] == "Test"
        assert "id" in result
        mock_cal.save_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_event_failure(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock(side_effect=Exception("server down"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Test", date="2026-02-14", time="10:00")
            with pytest.raises(CalendarError):
                await adapter.add_event(parsed)


class TestCalDAVFindEvents:
    @pytest.mark.asyncio
    async def test_find_events_success(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[_make_caldav_event()])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            events = await adapter.find_events(target_date="2026-02-14")

        assert len(events) == 1
        assert events[0]["summary"] == "Test Event"

    @pytest.mark.asyncio
    async def test_find_events_with_query_filter(self):
        mock_cal = MagicMock()
        ev1 = _make_caldav_event(summary="Meeting")
        ev2 = _make_caldav_event(uid="uid2", summary="Lunch")
        mock_cal.search = MagicMock(return_value=[ev1, ev2])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            events = await adapter.find_events(query="meeting", target_date="2026-02-14")

        assert len(events) == 1
        assert events[0]["summary"] == "Meeting"

    @pytest.mark.asyncio
    async def test_find_events_empty(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            events = await adapter.find_events(target_date="2026-02-14")

        assert events == []

    @pytest.mark.asyncio
    async def test_find_events_failure(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(side_effect=Exception("fail"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.find_events(target_date="2026-02-14")


class TestCalDAVDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_success(self):
        ev = _make_caldav_event(uid="del-uid")
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[ev])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            await adapter.delete_event("del-uid")

        ev.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError, match="not found"):
                await adapter.delete_event("nonexistent-uid")

    @pytest.mark.asyncio
    async def test_delete_failure(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(side_effect=Exception("fail"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.delete_event("uid")


class TestCalDAVUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_success(self):
        ev = _make_caldav_event(uid="upd-uid")
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[ev])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            result = await adapter.update_event("upd-uid", "2026-02-15", "14:00")

        assert result["id"] == "upd-uid"
        assert "14:00" in result["start_time"]
        ev.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError, match="not found"):
                await adapter.update_event("nonexistent", "2026-02-15", "14:00")

    @pytest.mark.asyncio
    async def test_update_failure(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(side_effect=Exception("fail"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.update_event("uid", "2026-02-15", "14:00")


class TestCalDAVAddRecurringEvent:
    @pytest.mark.asyncio
    async def test_recurring_weekly_success(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            result = await adapter.add_recurring_event(
                summary="Chore",
                description="Test",
                start_date="2026-02-08",
                start_time="17:00",
                end_time="17:30",
                frequency_days=7,
                occurrences=4,
            )

        assert result["summary"] == "Chore"
        assert "id" in result
        mock_cal.save_event.assert_called_once()
        # Verify RRULE was included in the saved iCal data
        saved_ical = mock_cal.save_event.call_args[0][0]
        assert "RRULE:" in saved_ical
        assert "FREQ=WEEKLY" in saved_ical

    @pytest.mark.asyncio
    async def test_recurring_daily_success(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            result = await adapter.add_recurring_event(
                summary="Daily",
                description="",
                start_date="2026-02-08",
                start_time="09:00",
                end_time="09:30",
                frequency_days=1,
                occurrences=7,
            )

        saved_ical = mock_cal.save_event.call_args[0][0]
        assert "FREQ=DAILY" in saved_ical

    @pytest.mark.asyncio
    async def test_recurring_interval_success(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            await adapter.add_recurring_event(
                summary="Every 3 days",
                description="",
                start_date="2026-02-08",
                start_time="10:00",
                end_time="10:45",
                frequency_days=3,
                occurrences=10,
            )

        saved_ical = mock_cal.save_event.call_args[0][0]
        assert "INTERVAL" in saved_ical

    @pytest.mark.asyncio
    async def test_recurring_failure(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock(side_effect=Exception("fail"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
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


class TestCalDAVAddEventWithGuests:
    @pytest.mark.asyncio
    async def test_add_event_with_guests(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Meeting", date="2026-02-14", time="10:00", guests=["a@test.com"])
            await adapter.add_event(parsed)

        saved_ical = mock_cal.save_event.call_args[0][0]
        assert "ATTENDEE" in saved_ical
        assert "mailto:a@test.com" in saved_ical

    @pytest.mark.asyncio
    async def test_add_event_without_guests(self):
        mock_cal = MagicMock()
        mock_cal.save_event = MagicMock()

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            from src.core.parser import ParsedEvent
            parsed = ParsedEvent(event="Meeting", date="2026-02-14", time="10:00")
            await adapter.add_event(parsed)

        saved_ical = mock_cal.save_event.call_args[0][0]
        assert "ATTENDEE" not in saved_ical


class TestCalDAVAddGuests:
    @pytest.mark.asyncio
    async def test_add_guests_success(self):
        ev = _make_caldav_event(uid="guest-uid")
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[ev])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            result = await adapter.add_guests("guest-uid", ["new@test.com"])

        assert result["id"] == "guest-uid"
        ev.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_guests_not_found(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError, match="not found"):
                await adapter.add_guests("nonexistent-uid", ["a@test.com"])

    @pytest.mark.asyncio
    async def test_add_guests_failure(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(side_effect=Exception("fail"))

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            with pytest.raises(CalendarError):
                await adapter.add_guests("uid", ["a@test.com"])


class TestCalDAVGetDailyEvents:
    @pytest.mark.asyncio
    async def test_delegates_to_find_events(self):
        mock_cal = MagicMock()
        mock_cal.search = MagicMock(return_value=[_make_caldav_event()])

        with patch(_PATCH_GET_CAL, return_value=mock_cal):
            adapter = CalDAVCalendarAdapter()
            events = await adapter.get_daily_events(target_date="2026-02-14")

        assert len(events) == 1
