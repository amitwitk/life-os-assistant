"""Tests for src.core.chore_scheduler — slot finding logic."""

import pytest
from unittest.mock import AsyncMock, patch

from src.core.chore_scheduler import (
    find_best_slot,
    _time_str_to_minutes,
    _overlaps_any,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestTimeStrToMinutes:
    def test_hhmm_format(self):
        assert _time_str_to_minutes("17:30") == 17 * 60 + 30

    def test_iso_datetime(self):
        assert _time_str_to_minutes("2026-02-07T09:15:00+03:00") == 9 * 60 + 15

    def test_midnight(self):
        assert _time_str_to_minutes("00:00") == 0

    def test_empty_string(self):
        assert _time_str_to_minutes("") is None

    def test_invalid_format(self):
        assert _time_str_to_minutes("not-a-time") is None


class TestOverlapsAny:
    def test_no_overlap(self):
        busy = [(60, 120), (180, 240)]  # 1:00-2:00, 3:00-4:00
        assert _overlaps_any(130, 170, busy) is False

    def test_overlap_start(self):
        busy = [(60, 120)]
        assert _overlaps_any(100, 150, busy) is True

    def test_overlap_end(self):
        busy = [(60, 120)]
        assert _overlaps_any(30, 90, busy) is True

    def test_fully_contained(self):
        busy = [(60, 180)]
        assert _overlaps_any(90, 120, busy) is True

    def test_no_busy(self):
        assert _overlaps_any(60, 120, []) is False


# ---------------------------------------------------------------------------
# Integration test for find_best_slot
# ---------------------------------------------------------------------------


class TestFindBestSlot:
    @pytest.mark.asyncio
    async def test_finds_slot_with_empty_calendar(self):
        mock_find = AsyncMock(return_value=[])
        with patch("src.integrations.gcal_service.find_events", mock_find):
            slot = await find_best_slot(
                chore_name="Test chore",
                frequency_days=7,
                duration_minutes=30,
                preferred_start="17:00",
                preferred_end="21:00",
                weeks_ahead=4,
            )
        assert slot is not None
        assert slot["start_time"] == "17:00"
        assert slot["end_time"] == "17:30"
        assert slot["occurrences"] == 4
        assert slot["frequency_days"] == 7

    @pytest.mark.asyncio
    async def test_avoids_busy_slot(self):
        """If 17:00-17:30 is busy, scheduler should pick 17:30 or later."""
        mock_find = AsyncMock(return_value=[
            {"start_time": "17:00", "end_time": "17:45", "summary": "Existing"},
        ])
        with patch("src.integrations.gcal_service.find_events", mock_find):
            slot = await find_best_slot(
                chore_name="Test chore",
                frequency_days=7,
                duration_minutes=30,
                preferred_start="17:00",
                preferred_end="21:00",
                weeks_ahead=2,
            )
        assert slot is not None
        # Should have picked 17:45 or later (next 15-min boundary after busy period)
        start_minutes = int(slot["start_time"].split(":")[0]) * 60 + int(slot["start_time"].split(":")[1])
        assert start_minutes >= 17 * 60 + 45

    @pytest.mark.asyncio
    async def test_picks_least_conflicted_slot_when_all_busy(self):
        """If the entire preferred window is busy, scheduler still returns the
        best-scored slot (least conflicts) rather than None — it's a best-effort
        approach since future days may be free."""
        mock_find = AsyncMock(return_value=[
            {"start_time": "17:00", "end_time": "21:00", "summary": "All day busy"},
        ])
        with patch("src.integrations.gcal_service.find_events", mock_find):
            slot = await find_best_slot(
                chore_name="Test chore",
                frequency_days=7,
                duration_minutes=30,
                preferred_start="17:00",
                preferred_end="21:00",
                weeks_ahead=2,
            )
        # Still returns a slot (best-effort); score is 0 but slot exists
        assert slot is not None
        assert slot["start_time"] == "17:00"

    @pytest.mark.asyncio
    async def test_returns_none_when_duration_exceeds_window(self):
        mock_find = AsyncMock(return_value=[])
        with patch("src.integrations.gcal_service.find_events", mock_find):
            slot = await find_best_slot(
                chore_name="Long chore",
                frequency_days=7,
                duration_minutes=300,  # 5 hours
                preferred_start="17:00",
                preferred_end="18:00",  # 1 hour window
                weeks_ahead=2,
            )
        assert slot is None

    @pytest.mark.asyncio
    async def test_calendar_error_gracefully_handled(self):
        """If calendar API fails, scheduler should still find slots (assumes free)."""
        mock_find = AsyncMock(side_effect=Exception("API error"))
        with patch("src.integrations.gcal_service.find_events", mock_find):
            slot = await find_best_slot(
                chore_name="Test",
                frequency_days=7,
                duration_minutes=30,
                preferred_start="09:00",
                preferred_end="21:00",
                weeks_ahead=2,
            )
        # Should still return a slot (defaults to free schedule)
        assert slot is not None
