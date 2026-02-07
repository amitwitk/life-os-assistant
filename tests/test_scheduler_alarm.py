"""Tests for nightly alarm functions in src.core.scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.scheduler import (
    _format_alarm_message,
    _get_travel_for_event,
    _send_alarm_for_user,
    send_nightly_alarm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    telegram_user_id: int = 12345,
    onboarded: bool = True,
    home_address: str | None = None,
    calendar_token_json: str | None = '{"token": "abc"}',
):
    user = MagicMock()
    user.telegram_user_id = telegram_user_id
    user.onboarded = onboarded
    user.home_address = home_address
    user.calendar_token_json = calendar_token_json
    return user


def _make_timed_event(
    summary: str = "Standup",
    start_time: str = "2025-01-15T09:00:00+02:00",
    location: str | None = None,
):
    event = {"summary": summary, "start_time": start_time}
    if location:
        event["location"] = location
    return event


# ---------------------------------------------------------------------------
# send_nightly_alarm
# ---------------------------------------------------------------------------


class TestSendNightlyAlarm:
    @pytest.mark.asyncio
    async def test_no_user_db_does_nothing(self):
        notifier = AsyncMock()
        await send_nightly_alarm(notifier, user_db=None)
        notifier.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_onboarded_users(self):
        notifier = AsyncMock()
        user_db = MagicMock()
        user_db.list_users.return_value = [_make_user(onboarded=False)]

        with patch("src.adapters.calendar_factory.create_calendar_adapter") as mock_cal:
            await send_nightly_alarm(notifier, user_db=user_db)

        mock_cal.assert_not_called()
        notifier.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_alarm_to_onboarded_user(self):
        notifier = AsyncMock()
        user = _make_user()
        user_db = MagicMock()
        user_db.list_users.return_value = [user]

        mock_cal = AsyncMock()
        mock_cal.get_daily_events = AsyncMock(return_value=[
            _make_timed_event(),
        ])

        with patch("src.adapters.calendar_factory.create_calendar_adapter", return_value=mock_cal):
            await send_nightly_alarm(notifier, user_db=user_db)

        notifier.send_message.assert_called_once()
        call_args = notifier.send_message.call_args
        assert call_args[0][0] == 12345
        assert "alarm" in call_args[0][1].lower() or "Standup" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_handles_user_error_gracefully(self):
        notifier = AsyncMock()
        user = _make_user()
        user_db = MagicMock()
        user_db.list_users.return_value = [user]

        with patch(
            "src.adapters.calendar_factory.create_calendar_adapter",
            side_effect=Exception("Calendar error"),
        ):
            # Should not raise
            await send_nightly_alarm(notifier, user_db=user_db)

        notifier.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# _send_alarm_for_user
# ---------------------------------------------------------------------------


class TestSendAlarmForUser:
    @pytest.mark.asyncio
    async def test_no_events_returns_none(self):
        user = _make_user()
        calendar = AsyncMock()
        calendar.get_daily_events = AsyncMock(return_value=[])
        notifier = AsyncMock()

        result = await _send_alarm_for_user(user, calendar, notifier)

        assert result is None
        notifier.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_day_only_returns_none(self):
        user = _make_user()
        calendar = AsyncMock()
        calendar.get_daily_events = AsyncMock(return_value=[
            {"summary": "Holiday", "start_time": "2025-01-15"},
        ])
        notifier = AsyncMock()

        result = await _send_alarm_for_user(user, calendar, notifier)

        assert result is None
        notifier.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_alarm_for_timed_event(self):
        user = _make_user()
        calendar = AsyncMock()
        calendar.get_daily_events = AsyncMock(return_value=[
            _make_timed_event(summary="Team sync", start_time="2025-01-15T10:00:00+02:00"),
        ])
        notifier = AsyncMock()

        with patch("src.core.scheduler._get_travel_for_event", return_value=(None, None)):
            result = await _send_alarm_for_user(user, calendar, notifier)

        assert result is not None
        assert "09:00" in result  # 10:00 - 60 min prep
        assert "Team sync" in result
        notifier.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_includes_travel_time(self):
        user = _make_user(home_address="Home St")
        calendar = AsyncMock()
        calendar.get_daily_events = AsyncMock(return_value=[
            _make_timed_event(
                summary="Client meeting",
                start_time="2025-01-15T10:00:00+02:00",
                location="Office Blvd",
            ),
        ])
        notifier = AsyncMock()

        with patch(
            "src.core.scheduler._get_travel_for_event",
            return_value=(30, "30 mins (25 km)"),
        ):
            result = await _send_alarm_for_user(user, calendar, notifier)

        assert "08:30" in result  # 10:00 - 60 prep - 30 travel
        assert "30 mins (25 km)" in result

    @pytest.mark.asyncio
    async def test_late_start_message(self):
        user = _make_user()
        calendar = AsyncMock()
        calendar.get_daily_events = AsyncMock(return_value=[
            _make_timed_event(start_time="2025-01-15T14:00:00+02:00"),
        ])
        notifier = AsyncMock()

        with patch("src.core.scheduler._get_travel_for_event", return_value=(None, None)):
            result = await _send_alarm_for_user(user, calendar, notifier)

        assert "relaxed morning" in result


# ---------------------------------------------------------------------------
# _get_travel_for_event
# ---------------------------------------------------------------------------


class TestGetTravelForEvent:
    @pytest.mark.asyncio
    async def test_no_home_address(self):
        user = _make_user(home_address=None)
        event = _make_timed_event(location="Office")
        minutes, text = await _get_travel_for_event(user, event)
        assert minutes is None
        assert text is None

    @pytest.mark.asyncio
    async def test_no_event_location(self):
        user = _make_user(home_address="Home St")
        event = _make_timed_event()  # no location
        minutes, text = await _get_travel_for_event(user, event)
        assert minutes is None
        assert text is None

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        user = _make_user(home_address="Home St")
        event = _make_timed_event(location="Office")
        with patch("src.core.scheduler.settings") as mock_settings:
            mock_settings.GOOGLE_MAPS_API_KEY = ""
            mock_settings.DEFAULT_PREP_TIME_MINUTES = 60
            minutes, text = await _get_travel_for_event(user, event)
        assert minutes is None

    @pytest.mark.asyncio
    async def test_successful_travel_lookup(self):
        user = _make_user(home_address="Home St")
        event = _make_timed_event(location="Office Blvd")

        mock_result = MagicMock()
        mock_result.duration_minutes = 25
        mock_result.duration_text = "25 mins"
        mock_result.distance_text = "20 km"

        with patch("src.core.scheduler.settings") as mock_settings, \
             patch("src.integrations.google_maps.get_travel_time", return_value=mock_result):
            mock_settings.GOOGLE_MAPS_API_KEY = "fake-key"
            minutes, text = await _get_travel_for_event(user, event)

        assert minutes == 25
        assert "25 mins" in text
        assert "20 km" in text

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        user = _make_user(home_address="Home St")
        event = _make_timed_event(location="Office")

        with patch("src.core.scheduler.settings") as mock_settings, \
             patch(
                 "src.integrations.google_maps.get_travel_time",
                 side_effect=Exception("API down"),
             ):
            mock_settings.GOOGLE_MAPS_API_KEY = "fake-key"
            minutes, text = await _get_travel_for_event(user, event)

        assert minutes is None
        assert text is None


# ---------------------------------------------------------------------------
# _format_alarm_message
# ---------------------------------------------------------------------------


class TestFormatAlarmMessage:
    def test_basic_message(self):
        from src.core.alarm_calculator import AlarmRecommendation

        rec = AlarmRecommendation(
            alarm_time="08:00",
            event_summary="Standup",
            event_start="09:00",
            prep_minutes=60,
            travel_minutes=None,
            travel_text=None,
        )
        msg = _format_alarm_message(rec, late=False)
        assert "08:00" in msg
        assert "Standup" in msg
        assert "60 min" in msg
        assert "Travel" not in msg
        assert "relaxed" not in msg

    def test_with_travel(self):
        from src.core.alarm_calculator import AlarmRecommendation

        rec = AlarmRecommendation(
            alarm_time="07:30",
            event_summary="Meeting",
            event_start="09:00",
            prep_minutes=60,
            travel_minutes=30,
            travel_text="30 mins (25 km)",
        )
        msg = _format_alarm_message(rec, late=False)
        assert "Travel: 30 mins (25 km)" in msg

    def test_late_start_note(self):
        from src.core.alarm_calculator import AlarmRecommendation

        rec = AlarmRecommendation(
            alarm_time="13:00",
            event_summary="Lunch meeting",
            event_start="14:00",
            prep_minutes=60,
            travel_minutes=None,
            travel_text=None,
        )
        msg = _format_alarm_message(rec, late=True)
        assert "relaxed morning" in msg
