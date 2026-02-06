"""Tests for src.core.parser — LLM-based message parsing."""

import pytest
from unittest.mock import AsyncMock, patch

from src.core.parser import (
    ParsedEvent,
    CancelEvent,
    RescheduleEvent,
    QueryEvents,
    parse_message,
    match_event,
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
# Tests for parse_message (LLM mocked)
# ---------------------------------------------------------------------------


class TestParseMessage:
    @pytest.mark.asyncio
    async def test_parse_create_event(self):
        llm_response = '{"intent": "create", "event": "Dentist", "date": "2026-02-14", "time": "16:00", "duration_minutes": 60, "description": ""}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Dentist tomorrow at 4pm")
        assert isinstance(result, ParsedEvent)
        assert result.event == "Dentist"
        assert result.date == "2026-02-14"

    @pytest.mark.asyncio
    async def test_parse_cancel_event(self):
        llm_response = '{"intent": "cancel", "event_summary": "Dentist", "date": "2026-02-14"}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Cancel my dentist appointment")
        assert isinstance(result, CancelEvent)
        assert result.event_summary == "Dentist"

    @pytest.mark.asyncio
    async def test_parse_reschedule_event(self):
        llm_response = '{"intent": "reschedule", "event_summary": "Meeting", "original_date": "2026-02-14", "new_time": "15:00"}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Move meeting to 3pm")
        assert isinstance(result, RescheduleEvent)
        assert result.new_time == "15:00"

    @pytest.mark.asyncio
    async def test_parse_query_event(self):
        llm_response = '{"intent": "query", "date": "2026-02-14"}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("What do I have tomorrow?")
        assert isinstance(result, QueryEvents)

    @pytest.mark.asyncio
    async def test_parse_null_response(self):
        with patch("src.core.parser.complete", AsyncMock(return_value="null")):
            result = await parse_message("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        with patch("src.core.parser.complete", AsyncMock(return_value="not json")):
            result = await parse_message("Something")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_unknown_intent(self):
        llm_response = '{"intent": "unknown_thing", "data": "whatever"}'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Something weird")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_with_code_block(self):
        llm_response = '```json\n{"intent": "create", "event": "Lunch", "date": "2026-02-14", "time": "12:00", "duration_minutes": 60, "description": ""}\n```'
        with patch("src.core.parser.complete", AsyncMock(return_value=llm_response)):
            result = await parse_message("Lunch tomorrow")
        assert isinstance(result, ParsedEvent)
        assert result.event == "Lunch"

    @pytest.mark.asyncio
    async def test_parse_llm_exception(self):
        with patch("src.core.parser.complete", AsyncMock(side_effect=Exception("API error"))):
            result = await parse_message("Anything")
        assert result is None


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
