"""Tests for src.core.conflict_checker — conflict detection and free slot finding."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.conflict_checker import (
    ConflictResult,
    check_conflict,
    extract_event_duration_minutes,
    find_nearest_free_slot,
)


# ---------------------------------------------------------------------------
# Tests for extract_event_duration_minutes
# ---------------------------------------------------------------------------


class TestExtractEventDurationMinutes:
    def test_iso_times(self):
        event = {
            "start_time": "2026-02-07T14:00:00+03:00",
            "end_time": "2026-02-07T15:30:00+03:00",
        }
        assert extract_event_duration_minutes(event) == 90

    def test_hhmm_times(self):
        event = {"start_time": "09:00", "end_time": "10:00"}
        assert extract_event_duration_minutes(event) == 60

    def test_missing_times_defaults_to_60(self):
        assert extract_event_duration_minutes({}) == 60

    def test_missing_end_time_defaults_to_60(self):
        event = {"start_time": "09:00"}
        assert extract_event_duration_minutes(event) == 60

    def test_same_start_end_defaults_to_60(self):
        event = {"start_time": "09:00", "end_time": "09:00"}
        assert extract_event_duration_minutes(event) == 60


# ---------------------------------------------------------------------------
# Tests for find_nearest_free_slot
# ---------------------------------------------------------------------------


class TestFindNearestFreeSlot:
    def test_finds_slot_after_busy(self):
        # Busy 10:00-11:00 (600-660), request at 10:00, duration 30
        busy = [(600, 660)]
        result = find_nearest_free_slot(busy, 30, 600)
        assert result == "11:00"

    def test_finds_slot_before_busy(self):
        # Busy 10:00-22:00 (600-1320), request at 10:00, duration 30
        # Forward search fails (day_end=1320), backward should find 09:30
        busy = [(600, 1320)]
        result = find_nearest_free_slot(busy, 30, 600)
        assert result == "09:30"

    def test_returns_none_when_day_fully_booked(self):
        # Busy from day_start to day_end
        busy = [(420, 1320)]
        result = find_nearest_free_slot(busy, 60, 600)
        assert result is None

    def test_respects_day_bounds(self):
        # Busy 07:00-22:00, duration 60 — no room
        busy = [(420, 1320)]
        result = find_nearest_free_slot(busy, 60, 600, day_start=420, day_end=1320)
        assert result is None

    def test_finds_gap_between_events(self):
        # Busy 10:00-11:00 and 12:00-13:00 — gap at 11:00-12:00
        busy = [(600, 660), (720, 780)]
        result = find_nearest_free_slot(busy, 30, 600)
        assert result == "11:00"

    def test_custom_day_bounds(self):
        # Busy 08:00-09:00, request 08:00, day_start=07:00
        busy = [(480, 540)]
        result = find_nearest_free_slot(busy, 30, 480, day_start=420, day_end=1320)
        assert result == "09:00"

    def test_empty_busy_returns_next_slot(self):
        # No busy intervals — function skips the requested_start itself
        # (it's only called when the requested time has a conflict) and
        # returns the next 15-min increment
        busy = []
        result = find_nearest_free_slot(busy, 30, 600)
        assert result == "10:15"


# ---------------------------------------------------------------------------
# Tests for check_conflict
# ---------------------------------------------------------------------------


def _mock_calendar(events=None, side_effect=None):
    cal = MagicMock()
    if side_effect:
        cal.find_events = AsyncMock(side_effect=side_effect)
    else:
        cal.find_events = AsyncMock(return_value=events or [])
    return cal


class TestCheckConflict:
    @pytest.mark.asyncio
    async def test_no_conflict(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Lunch", "start_time": "12:00", "end_time": "13:00"},
        ])
        result = await check_conflict(cal, "2026-02-07", "14:00", 60)
        assert result.has_conflict is False
        assert result.conflicting_events == []

    @pytest.mark.asyncio
    async def test_overlap_detected(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Meeting", "start_time": "14:00", "end_time": "15:00"},
        ])
        result = await check_conflict(cal, "2026-02-07", "14:30", 60)
        assert result.has_conflict is True
        assert len(result.conflicting_events) == 1
        assert result.conflicting_events[0]["summary"] == "Meeting"

    @pytest.mark.asyncio
    async def test_exclude_self_for_reschedule(self):
        cal = _mock_calendar(events=[
            {"id": "ev1", "summary": "My Event", "start_time": "14:00", "end_time": "15:00"},
        ])
        # Without exclusion → conflict
        result = await check_conflict(cal, "2026-02-07", "14:00", 60)
        assert result.has_conflict is True

        # With self-exclusion → no conflict
        result = await check_conflict(cal, "2026-02-07", "14:00", 60, exclude_event_id="ev1")
        assert result.has_conflict is False

    @pytest.mark.asyncio
    async def test_suggests_alternative_time(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Busy", "start_time": "14:00", "end_time": "15:00"},
        ])
        result = await check_conflict(cal, "2026-02-07", "14:00", 60)
        assert result.has_conflict is True
        assert result.suggested_time == "15:00"

    @pytest.mark.asyncio
    async def test_skips_all_day_events(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Holiday", "start_time": "", "end_time": ""},
        ])
        result = await check_conflict(cal, "2026-02-07", "10:00", 60)
        assert result.has_conflict is False

    @pytest.mark.asyncio
    async def test_calendar_error_returns_no_conflict(self):
        cal = _mock_calendar(side_effect=Exception("API down"))
        result = await check_conflict(cal, "2026-02-07", "10:00", 60)
        assert result.has_conflict is False

    @pytest.mark.asyncio
    async def test_invalid_start_time_returns_no_conflict(self):
        cal = _mock_calendar(events=[])
        result = await check_conflict(cal, "2026-02-07", "invalid", 60)
        assert result.has_conflict is False
