"""Tests for src.core.alarm_calculator — pure alarm logic."""

from src.core.alarm_calculator import (
    AlarmRecommendation,
    build_alarm_recommendation,
    calculate_alarm_time,
    find_first_timed_event,
    is_late_start,
)


class TestFindFirstTimedEvent:
    def test_returns_earliest_timed_event(self):
        events = [
            {"summary": "Lunch", "start_time": "2025-01-15T12:00:00+02:00"},
            {"summary": "Standup", "start_time": "2025-01-15T09:00:00+02:00"},
            {"summary": "Dinner", "start_time": "2025-01-15T19:00:00+02:00"},
        ]
        result = find_first_timed_event(events)
        assert result["summary"] == "Standup"

    def test_skips_all_day_events(self):
        events = [
            {"summary": "Holiday", "start_time": "2025-01-15"},
            {"summary": "Meeting", "start_time": "2025-01-15T10:00:00+02:00"},
        ]
        result = find_first_timed_event(events)
        assert result["summary"] == "Meeting"

    def test_all_day_only_returns_none(self):
        events = [
            {"summary": "Holiday", "start_time": "2025-01-15"},
            {"summary": "Birthday", "start_time": "2025-01-16"},
        ]
        assert find_first_timed_event(events) is None

    def test_empty_list_returns_none(self):
        assert find_first_timed_event([]) is None

    def test_missing_start_time_skipped(self):
        events = [
            {"summary": "No time"},
            {"summary": "Has time", "start_time": "2025-01-15T08:00:00"},
        ]
        result = find_first_timed_event(events)
        assert result["summary"] == "Has time"


class TestCalculateAlarmTime:
    def test_basic_calculation(self):
        assert calculate_alarm_time("2025-01-15T09:00:00+02:00", 60) == "08:00"

    def test_with_travel_time(self):
        assert calculate_alarm_time("2025-01-15T09:00:00+02:00", 60, 30) == "07:30"

    def test_wraps_past_midnight(self):
        """Very early event + prep wraps to previous day's time."""
        result = calculate_alarm_time("2025-01-15T01:00:00+02:00", 120)
        assert result == "23:00"

    def test_hhmm_input(self):
        assert calculate_alarm_time("08:30", 30) == "08:00"

    def test_zero_prep(self):
        assert calculate_alarm_time("2025-01-15T10:00:00", 0) == "10:00"

    def test_zero_travel(self):
        assert calculate_alarm_time("2025-01-15T10:00:00", 60, 0) == "09:00"


class TestBuildAlarmRecommendation:
    def test_basic_recommendation(self):
        event = {
            "summary": "Standup",
            "start_time": "2025-01-15T09:00:00+02:00",
        }
        rec = build_alarm_recommendation(event, prep_minutes=60)
        assert isinstance(rec, AlarmRecommendation)
        assert rec.alarm_time == "08:00"
        assert rec.event_summary == "Standup"
        assert rec.event_start == "09:00"
        assert rec.prep_minutes == 60
        assert rec.travel_minutes is None
        assert rec.travel_text is None

    def test_with_travel(self):
        event = {
            "summary": "Client meeting",
            "start_time": "2025-01-15T10:00:00+02:00",
        }
        rec = build_alarm_recommendation(
            event, prep_minutes=60, travel_minutes=30, travel_text="30 mins (25 km)",
        )
        assert rec.alarm_time == "08:30"
        assert rec.travel_minutes == 30
        assert rec.travel_text == "30 mins (25 km)"

    def test_no_title_uses_default(self):
        event = {"start_time": "2025-01-15T09:00:00"}
        rec = build_alarm_recommendation(event, prep_minutes=30)
        assert rec.event_summary == "(no title)"


class TestIsLateStart:
    def test_morning_event_not_late(self):
        assert is_late_start("2025-01-15T08:00:00+02:00") is False

    def test_noon_event_is_late(self):
        assert is_late_start("2025-01-15T12:00:00+02:00") is True

    def test_afternoon_event_is_late(self):
        assert is_late_start("2025-01-15T14:30:00+02:00") is True

    def test_custom_threshold(self):
        assert is_late_start("2025-01-15T10:00:00+02:00", threshold_hour=10) is True
        assert is_late_start("2025-01-15T09:59:00+02:00", threshold_hour=10) is False

    def test_hhmm_input(self):
        assert is_late_start("13:00") is True
        assert is_late_start("11:00") is False
