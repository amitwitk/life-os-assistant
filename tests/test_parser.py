"""Tests for src.core.parser — LLM-based message parsing."""

import pytest
from unittest.mock import AsyncMock, patch

from src.core.parser import (
    ParsedEvent,
    CancelEvent,
    CancelAllExcept,
    RescheduleEvent,
    QueryEvents,
    parse_message,
    match_event,
    batch_match_events,
    batch_exclude_events,
    _clean_llm_response,
)


# ---------------------------------------------------------------------------
# Unit tests for _clean_llm_response
# ---------------------------------------------------------------------------


class TestCleanLlmResponse:
    def test_strips_json_code_block(self):
        raw = '```json\n{"intent": "create"}\n```'
        assert _clean_llm_response(raw) == '{"intent": "create"}'

    def test_strips_whitespace(self):
        assert _clean_llm_response("  hello  ") == "hello"

    def test_no_code_block(self):
        assert _clean_llm_response('{"intent": "create"}') == '{"intent": "create"}'

    def test_null_string(self):
        assert _clean_llm_response("null") == "null"


# ---------------------------------------------------------------------------
# Tests for parse_message (LLM mocked) — now returns list
# ---------------------------------------------------------------------------


class TestParseMessage:
    @pytest.mark.asyncio
    async def test_parse_create_event(self):
        llm_response = '[{"intent": "create", "event": "Dentist", "date": "2026-02-14", "time": "16:00", "duration_minutes": 60, "description": ""}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Dentist tomorrow at 4pm")
        assert len(result) == 1
        assert isinstance(result[0], ParsedEvent)
        assert result[0].event == "Dentist"
        assert result[0].date == "2026-02-14"

    @pytest.mark.asyncio
    async def test_parse_cancel_event(self):
        llm_response = '[{"intent": "cancel", "event_summary": "Dentist", "date": "2026-02-14"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Cancel my dentist appointment")
        assert len(result) == 1
        assert isinstance(result[0], CancelEvent)
        assert result[0].event_summary == "Dentist"

    @pytest.mark.asyncio
    async def test_parse_reschedule_event(self):
        llm_response = '[{"intent": "reschedule", "event_summary": "Meeting", "original_date": "2026-02-14", "new_time": "15:00"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Move meeting to 3pm")
        assert len(result) == 1
        assert isinstance(result[0], RescheduleEvent)
        assert result[0].new_time == "15:00"

    @pytest.mark.asyncio
    async def test_parse_query_event(self):
        llm_response = '[{"intent": "query", "date": "2026-02-14"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("What do I have tomorrow?")
        assert len(result) == 1
        assert isinstance(result[0], QueryEvents)

    @pytest.mark.asyncio
    async def test_parse_null_response(self):
        with patch("src.core.parser.complete", AsyncMock(return_value="null")):
            result = await parse_message("Hello")
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        with patch("src.core.parser.complete", AsyncMock(return_value="not json")):
            result = await parse_message("Something")
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_unknown_intent(self):
        llm_response = '[{"intent": "unknown_thing", "data": "whatever"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Something weird")
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_with_code_block(self):
        llm_response = '```json\n[{"intent": "create", "event": "Lunch", "date": "2026-02-14", "time": "12:00", "duration_minutes": 60, "description": ""}]\n```'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Lunch tomorrow")
        assert len(result) == 1
        assert isinstance(result[0], ParsedEvent)
        assert result[0].event == "Lunch"

    @pytest.mark.asyncio
    async def test_parse_llm_exception(self):
        with patch("src.core.parser.complete", AsyncMock(side_effect=Exception("API error"))):
            result = await parse_message("Anything")
        assert result == []


# ---------------------------------------------------------------------------
# New tests for multi-action parsing
# ---------------------------------------------------------------------------


class TestMultiActionParsing:
    @pytest.mark.asyncio
    async def test_parse_multiple_cancels(self):
        llm_response = '[{"intent": "cancel", "event_summary": "Meeting with Amit", "date": "2026-02-14"}, {"intent": "cancel", "event_summary": "Meeting with Shon", "date": "2026-02-14"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Cancel my meeting with Amit and my meeting with Shon")
        assert len(result) == 2
        assert all(isinstance(r, CancelEvent) for r in result)
        assert result[0].event_summary == "Meeting with Amit"
        assert result[1].event_summary == "Meeting with Shon"

    @pytest.mark.asyncio
    async def test_parse_cancel_all_except(self):
        llm_response = '[{"intent": "cancel_all_except", "date": "2026-02-14", "exceptions": ["Padel game"]}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Cancel all of my meetings today except the padel game")
        assert len(result) == 1
        assert isinstance(result[0], CancelAllExcept)
        assert result[0].exceptions == ["Padel game"]

    @pytest.mark.asyncio
    async def test_parse_mixed_actions(self):
        llm_response = '[{"intent": "create", "event": "Meeting with Dan", "date": "2026-02-14", "time": "14:00", "duration_minutes": 60, "description": ""}, {"intent": "cancel", "event_summary": "Dentist", "date": "2026-02-14"}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Set up meeting with Dan at 14:00 and cancel my dentist")
        assert len(result) == 2
        assert isinstance(result[0], ParsedEvent)
        assert isinstance(result[1], CancelEvent)

    @pytest.mark.asyncio
    async def test_parse_single_object_auto_wrapped(self):
        """LLM returns a single dict instead of a list — should be auto-wrapped."""
        llm_response = '{"intent": "create", "event": "Lunch", "date": "2026-02-14", "time": "12:00", "duration_minutes": 60, "description": ""}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Lunch tomorrow at noon")
        assert len(result) == 1
        assert isinstance(result[0], ParsedEvent)

    @pytest.mark.asyncio
    async def test_parse_empty_array(self):
        with patch("src.core.parser.complete", AsyncMock(return_value="[]")):
            result = await parse_message("Hello there")
        assert result == []


# ---------------------------------------------------------------------------
# Tests for match_event
# ---------------------------------------------------------------------------


class TestMatchEvent:
    @pytest.mark.asyncio
    async def test_match_returns_correct_event(self):
        events = [
            {"summary": "Team standup", "id": "1"},
            {"summary": "Dentist appointment", "id": "2"},
        ]
        with patch("src.core.parser.complete", AsyncMock(return_value="1")):
            result = await match_event("dentist", events)
        assert result is not None
        assert result["id"] == "2"

    @pytest.mark.asyncio
    async def test_match_returns_none_when_no_match(self):
        events = [{"summary": "Team standup", "id": "1"}]
        with patch("src.core.parser.complete", AsyncMock(return_value="none")):
            result = await match_event("dentist", events)
        assert result is None

    @pytest.mark.asyncio
    async def test_match_empty_events_list(self):
        result = await match_event("anything", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_match_llm_returns_out_of_range(self):
        events = [{"summary": "Only one", "id": "1"}]
        with patch("src.core.parser.complete", AsyncMock(return_value="5")):
            result = await match_event("test", events)
        assert result is None


# ---------------------------------------------------------------------------
# Tests for batch_match_events
# ---------------------------------------------------------------------------


class TestBatchMatchEvents:
    @pytest.mark.asyncio
    async def test_batch_match_basic(self):
        events = [
            {"summary": "Team standup", "id": "1"},
            {"summary": "Dentist appointment", "id": "2"},
            {"summary": "Lunch with Dan", "id": "3"},
        ]
        with patch("src.core.parser.complete", AsyncMock(return_value="[1, 2]")):
            result = await batch_match_events(["dentist", "lunch"], events)
        assert len(result) == 2
        assert result[0]["id"] == "2"
        assert result[1]["id"] == "3"

    @pytest.mark.asyncio
    async def test_batch_match_with_none(self):
        events = [
            {"summary": "Team standup", "id": "1"},
            {"summary": "Dentist appointment", "id": "2"},
        ]
        with patch("src.core.parser.complete", AsyncMock(return_value='[0, "none"]')):
            result = await batch_match_events(["standup", "yoga"], events)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1] is None

    @pytest.mark.asyncio
    async def test_batch_match_malformed_fallback(self):
        """If batch response is malformed, falls back to sequential match_event calls."""
        events = [
            {"summary": "Team standup", "id": "1"},
            {"summary": "Dentist appointment", "id": "2"},
        ]
        # First call (batch) returns garbage, then fallback calls return valid indices
        call_count = 0

        async def mock_complete(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "garbage"  # Batch call fails
            return "0"  # Sequential fallback calls

        with patch("src.core.parser.complete", mock_complete):
            result = await batch_match_events(["standup", "dentist"], events)
        assert len(result) == 2
        # Both fallback to match_event → index 0
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "1"

    @pytest.mark.asyncio
    async def test_batch_match_empty_events(self):
        result = await batch_match_events(["anything"], [])
        assert result == [None]

    @pytest.mark.asyncio
    async def test_batch_match_empty_descriptions(self):
        events = [{"summary": "Test", "id": "1"}]
        result = await batch_match_events([], events)
        assert result == []


# ---------------------------------------------------------------------------
# Tests for batch_exclude_events
# ---------------------------------------------------------------------------


class TestBatchExcludeEvents:
    @pytest.mark.asyncio
    async def test_exclude_keeps_correct_events(self):
        events = [
            {"summary": "Meeting with Amit", "id": "1"},
            {"summary": "Padel game", "id": "2"},
            {"summary": "Meeting with Shon", "id": "3"},
        ]
        # batch_match_events returns Padel game as matched exception
        with patch("src.core.parser.complete", AsyncMock(return_value="[1]")):
            result = await batch_exclude_events(["Padel game"], events)
        # Should return the two meetings (not the padel game)
        assert len(result) == 2
        ids = {ev["id"] for ev in result}
        assert "1" in ids
        assert "3" in ids

    @pytest.mark.asyncio
    async def test_exclude_no_exceptions_returns_all(self):
        events = [
            {"summary": "Meeting", "id": "1"},
            {"summary": "Lunch", "id": "2"},
        ]
        result = await batch_exclude_events([], events)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests for mentioned_contacts field
# ---------------------------------------------------------------------------


class TestMentionedContacts:
    def test_parsed_event_defaults_to_empty(self):
        p = ParsedEvent(event="Lunch", date="2026-02-14", time="12:00")
        assert p.mentioned_contacts == []
        assert p.guests == []

    @pytest.mark.asyncio
    async def test_parse_event_with_mentioned_contacts(self):
        llm_response = '[{"intent": "create", "event": "Meeting with Yahav", "date": "2026-02-14", "time": "16:00", "duration_minutes": 60, "description": "", "guests": [], "mentioned_contacts": ["Yahav"]}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Meeting with Yahav tomorrow at 4pm")
        assert len(result) == 1
        assert isinstance(result[0], ParsedEvent)
        assert result[0].mentioned_contacts == ["Yahav"]

    @pytest.mark.asyncio
    async def test_parse_event_with_guests(self):
        llm_response = '[{"intent": "create", "event": "Meeting", "date": "2026-02-14", "time": "16:00", "duration_minutes": 60, "description": "", "guests": ["dan@example.com"], "mentioned_contacts": []}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Meeting tomorrow with dan@example.com")
        assert len(result) == 1
        assert result[0].guests == ["dan@example.com"]

    @pytest.mark.asyncio
    async def test_parse_event_with_both(self):
        llm_response = '[{"intent": "create", "event": "Meeting", "date": "2026-02-14", "time": "16:00", "duration_minutes": 60, "description": "", "guests": ["dan@example.com"], "mentioned_contacts": ["Yahav"]}]'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Meeting with Yahav and dan@example.com")
        assert len(result) == 1
        assert result[0].guests == ["dan@example.com"]
        assert result[0].mentioned_contacts == ["Yahav"]

    def test_backward_compat_no_contacts_field(self):
        """Old LLM responses without guests/mentioned_contacts still work."""
        p = ParsedEvent(
            intent="create", event="Dentist", date="2026-02-14",
            time="16:00", duration_minutes=60, description="",
        )
        assert p.mentioned_contacts == []
        assert p.guests == []
