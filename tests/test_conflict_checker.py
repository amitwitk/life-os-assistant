"""Tests for src.core.conflict_checker — conflict detection and free slot finding."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.conflict_checker import (
    ConflictResult,
    FreeSlotResult,
    check_conflict,
    extract_event_duration_minutes,
    find_free_slots,
    find_nearest_free_slot,
    get_free_slots,
    spread_slots,
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


# ---------------------------------------------------------------------------
# Tests for find_free_slots
# ---------------------------------------------------------------------------


class TestFindFreeSlots:
    def test_empty_day_returns_max_slots(self):
        result = find_free_slots([], 60, max_slots=5)
        assert len(result) == 5
        assert result[0] == "08:00"
        assert result[1] == "08:30"
        assert result[2] == "09:00"
        assert result[3] == "09:30"
        assert result[4] == "10:00"

    def test_busy_block_finds_slots_around_it(self):
        # Busy 10:00-11:00 (600-660)
        busy = [(600, 660)]
        result = find_free_slots(busy, 60, max_slots=5)
        # Should include 08:00, 08:30, 09:00 (before busy) but NOT 10:00 or 10:30
        assert "08:00" in result
        assert "09:00" in result
        assert "10:00" not in result
        # 11:00 should be available
        assert "11:00" in result

    def test_respects_max_slots(self):
        result = find_free_slots([], 60, max_slots=3)
        assert len(result) == 3

    def test_respects_duration(self):
        # Busy 09:00-09:30 (540-570) — a 60-min slot at 08:30 would overlap
        busy = [(540, 570)]
        result = find_free_slots(busy, 60, max_slots=10)
        assert "08:30" not in result
        assert "08:00" in result  # 08:00-09:00 fits

    def test_respects_day_bounds(self):
        # day_start=600 (10:00), day_end=720 (12:00), duration 60
        result = find_free_slots([], 60, max_slots=10, day_start=600, day_end=720)
        assert result == ["10:00", "10:30", "11:00"]

    def test_fully_booked_returns_empty(self):
        # Busy from 08:00 to 20:00
        busy = [(480, 1200)]
        result = find_free_slots(busy, 60)
        assert result == []

    def test_all_day_events_ignored_implicitly(self):
        # All-day events have no start/end time — they aren't included in busy_intervals
        # by the caller. With empty busy, all slots should be available.
        result = find_free_slots([], 30, max_slots=3)
        assert len(result) == 3

    def test_current_minutes_filters_past_slots(self):
        # Current time is 10:15 → effective start rounds up to 10:30
        result = find_free_slots([], 60, max_slots=3, current_minutes=615)
        assert result[0] == "10:30"
        assert "08:00" not in result
        assert "10:00" not in result

    def test_current_minutes_on_30_min_boundary(self):
        # Current time is exactly 10:00 → start at 10:00
        result = find_free_slots([], 60, max_slots=3, current_minutes=600)
        assert result[0] == "10:00"

    def test_max_slots_zero_returns_all(self):
        # max_slots=0 means unlimited — should return all available
        result = find_free_slots([], 60, max_slots=0, day_start=600, day_end=720)
        assert result == ["10:00", "10:30", "11:00"]


# ---------------------------------------------------------------------------
# Tests for spread_slots
# ---------------------------------------------------------------------------


class TestSpreadSlots:
    def test_returns_all_when_fewer_than_max(self):
        slots = ["08:00", "09:00", "10:00"]
        assert spread_slots(slots, max_slots=5) == slots

    def test_returns_all_when_equal_to_max(self):
        slots = ["08:00", "09:00", "10:00", "11:00", "12:00"]
        assert spread_slots(slots, max_slots=5) == slots

    def test_spreads_evenly_across_range(self):
        # 10 slots, pick 5 → should include first and last
        all_slots = [
            "08:00", "08:30", "09:00", "09:30", "10:00",
            "10:30", "11:00", "11:30", "12:00", "12:30",
        ]
        result = spread_slots(all_slots, max_slots=5)
        assert len(result) == 5
        assert result[0] == "08:00"   # first
        assert result[-1] == "12:30"  # last
        # Should NOT be 5 consecutive slots
        assert result != all_slots[:5]

    def test_spread_single_slot(self):
        all_slots = ["08:00", "09:00", "10:00", "11:00", "12:00"]
        result = spread_slots(all_slots, max_slots=1)
        assert len(result) == 1
        assert result[0] == "10:00"  # middle

    def test_empty_input(self):
        assert spread_slots([], max_slots=5) == []


# ---------------------------------------------------------------------------
# Tests for get_free_slots
# ---------------------------------------------------------------------------


class TestGetFreeSlots:
    @pytest.mark.asyncio
    async def test_returns_free_slot_result(self):
        cal = _mock_calendar(events=[])
        result = await get_free_slots(cal, "2099-01-01", 60, max_slots=3)
        assert isinstance(result, FreeSlotResult)
        assert len(result.suggested) == 3
        assert result.suggested[0] == "08:00"
        # all_available should have more slots than suggested
        assert len(result.all_available) > len(result.suggested)

    @pytest.mark.asyncio
    async def test_suggested_slots_are_spread(self):
        cal = _mock_calendar(events=[])
        result = await get_free_slots(cal, "2099-01-01", 60, max_slots=5)
        assert len(result.suggested) == 5
        # Verify spread: first and last should not be adjacent
        assert result.suggested[0] == "08:00"
        assert result.suggested[-1] != "10:00"  # NOT consecutive

    @pytest.mark.asyncio
    async def test_skips_busy_events(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Meeting", "start_time": "09:00", "end_time": "10:00"},
        ])
        result = await get_free_slots(cal, "2099-01-01", 60, max_slots=5)
        assert "09:00" not in result.all_available
        assert "09:30" not in result.all_available
        assert "08:00" in result.all_available

    @pytest.mark.asyncio
    async def test_calendar_error_returns_empty_result(self):
        cal = _mock_calendar(side_effect=Exception("API down"))
        result = await get_free_slots(cal, "2099-01-01", 60)
        assert isinstance(result, FreeSlotResult)
        assert result.suggested == []
        assert result.all_available == []

    @pytest.mark.asyncio
    async def test_all_day_events_ignored(self):
        cal = _mock_calendar(events=[
            {"id": "1", "summary": "Holiday", "start_time": "", "end_time": ""},
        ])
        result = await get_free_slots(cal, "2099-01-01", 60, max_slots=3)
        assert len(result.suggested) == 3

    @pytest.mark.asyncio
    async def test_today_filters_past_times(self):
        from unittest.mock import patch as mock_patch
        from datetime import date as d, datetime as dt

        cal = _mock_calendar(events=[])
        # Mock "today" and "now" to 14:00
        fake_today = d(2099, 1, 1)
        fake_now = dt(2099, 1, 1, 14, 0)
        with mock_patch("src.core.conflict_checker.date") as mock_date, \
             mock_patch("src.core.conflict_checker.datetime") as mock_datetime:
            mock_date.today.return_value = fake_today
            mock_datetime.now.return_value = fake_now
            result = await get_free_slots(cal, "2099-01-01", 60, max_slots=5)

        # No slot should be before 14:00
        for slot in result.all_available:
            h, m = map(int, slot.split(":"))
            assert h * 60 + m >= 14 * 60
