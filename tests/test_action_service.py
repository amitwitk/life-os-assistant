"""Tests for src.core.action_service — UI-agnostic service layer.

Tests the ActionService in isolation with a mocked CalendarPort.
No Telegram dependency anywhere in this file.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.action_service import (
    ActionResult,
    ActionService,
    BatchCancelPromptResponse,
    BatchSummaryResponse,
    ConflictPromptResponse,
    ContactPromptResponse,
    ErrorResponse,
    NoActionResponse,
    PendingBatchCancel,
    PendingContactResolution,
    PendingEvent,
    QueryResultResponse,
    ResponseKind,
    SlotSuggestionResponse,
    SuccessResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_service(calendar=None):
    """Create an ActionService with a mock calendar."""
    cal = calendar or MagicMock()
    return ActionService(cal), cal


# ---------------------------------------------------------------------------
# process_text — create events
# ---------------------------------------------------------------------------


class TestProcessTextCreate:
    @pytest.mark.asyncio
    async def test_create_no_conflict(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Meeting tomorrow at 14:00")

        assert isinstance(response, SuccessResponse)
        assert response.kind == ResponseKind.SUCCESS
        assert "Meeting" in response.message
        assert response.event is not None
        assert response.event.link == "https://cal/1"
        cal.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_conflict(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Existing meeting", "start_time": "14:00", "end_time": "15:00"}],
            suggested_time="15:00",
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=conflict)):
            response = await service.process_text("Meeting at 14:00")

        assert isinstance(response, ConflictPromptResponse)
        assert response.kind == ResponseKind.CONFLICT_PROMPT
        assert len(response.options) == 4  # suggested, force, custom, cancel
        assert response.options[0].key == "suggested"
        assert response.options[0].time == "15:00"
        assert response.pending is not None
        assert response.pending.pending_type == "create"
        assert "Existing meeting" in response.conflicting_summaries

    @pytest.mark.asyncio
    async def test_create_calendar_error(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult
        from src.ports.calendar_port import CalendarError

        service, cal = _make_service()
        cal.add_event = AsyncMock(side_effect=CalendarError("API error"))

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Meeting at 14:00")

        assert isinstance(response, ErrorResponse)
        assert "couldn't save" in response.message


# ---------------------------------------------------------------------------
# process_text — cancel events
# ---------------------------------------------------------------------------


class TestProcessTextCancel:
    @pytest.mark.asyncio
    async def test_cancel_success(self):
        from src.core.parser import CancelEvent

        service, cal = _make_service()
        events = [{"summary": "Meeting with Dan", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)
        cal.delete_event = AsyncMock()

        parsed = CancelEvent(event_summary="Meeting with Dan", date="2026-02-08")

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=events[0])):
            response = await service.process_text("Cancel meeting with Dan")

        assert isinstance(response, SuccessResponse)
        assert "canceled" in response.message.lower()
        cal.delete_event.assert_called_once_with("ev1")

    @pytest.mark.asyncio
    async def test_cancel_no_match(self):
        from src.core.parser import CancelEvent

        service, cal = _make_service()
        events = [{"summary": "Padel game", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)

        parsed = CancelEvent(event_summary="Meeting with Dan", date="2026-02-08")

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=None)):
            response = await service.process_text("Cancel meeting with Dan")

        assert isinstance(response, ErrorResponse)
        assert "couldn't match" in response.message.lower()

    @pytest.mark.asyncio
    async def test_cancel_no_events_on_date(self):
        from src.core.parser import CancelEvent

        service, cal = _make_service()
        cal.find_events = AsyncMock(return_value=[])

        parsed = CancelEvent(event_summary="Meeting", date="2026-02-08")

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("Cancel meeting")

        assert isinstance(response, ErrorResponse)
        assert "no events" in response.message.lower()


# ---------------------------------------------------------------------------
# process_text — reschedule events
# ---------------------------------------------------------------------------


class TestProcessTextReschedule:
    @pytest.mark.asyncio
    async def test_reschedule_success(self):
        from src.core.parser import RescheduleEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        matched = {"id": "ev1", "summary": "My Meeting", "start_time": "10:00", "end_time": "11:00"}
        cal.find_events = AsyncMock(return_value=[matched])
        cal.update_event = AsyncMock(return_value={"summary": "My Meeting", "htmlLink": "https://cal/1"})

        parsed = RescheduleEvent(event_summary="My Meeting", original_date="2026-02-08", new_time="15:00")
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=matched)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Reschedule meeting to 15:00")

        assert isinstance(response, SuccessResponse)
        assert "rescheduled" in response.message.lower()
        cal.update_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_reschedule_with_conflict(self):
        from src.core.parser import RescheduleEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        matched = {"id": "ev1", "summary": "My Meeting", "start_time": "10:00", "end_time": "11:00"}
        cal.find_events = AsyncMock(return_value=[matched])

        parsed = RescheduleEvent(event_summary="My Meeting", original_date="2026-02-08", new_time="14:00")
        conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Blocker", "start_time": "14:00", "end_time": "15:00"}],
            suggested_time="15:00",
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=matched)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=conflict)):
            response = await service.process_text("Reschedule meeting to 14:00")

        assert isinstance(response, ConflictPromptResponse)
        assert response.pending.pending_type == "reschedule"
        assert response.pending.event_id == "ev1"


# ---------------------------------------------------------------------------
# process_text — query events
# ---------------------------------------------------------------------------


class TestProcessTextQuery:
    @pytest.mark.asyncio
    async def test_query_with_events(self):
        from src.core.parser import QueryEvents

        service, cal = _make_service()
        events = [
            {"summary": "Meeting", "start_time": "2026-02-08T10:00:00", "end_time": "2026-02-08T11:00:00"},
            {"summary": "Lunch", "start_time": "2026-02-08T12:00:00", "end_time": "2026-02-08T13:00:00"},
        ]
        cal.find_events = AsyncMock(return_value=events)

        parsed = QueryEvents(date="2026-02-08")

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("What's on my schedule?")

        assert isinstance(response, QueryResultResponse)
        assert response.date == "2026-02-08"
        assert len(response.events) == 2
        assert "Meeting" in response.message

    @pytest.mark.asyncio
    async def test_query_no_events(self):
        from src.core.parser import QueryEvents

        service, cal = _make_service()
        cal.find_events = AsyncMock(return_value=[])

        parsed = QueryEvents(date="2026-02-08")

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("What do I have tomorrow?")

        assert isinstance(response, QueryResultResponse)
        assert "No events" in response.message


# ---------------------------------------------------------------------------
# process_text — cancel all except
# ---------------------------------------------------------------------------


class TestProcessTextCancelAllExcept:
    @pytest.mark.asyncio
    async def test_cancel_all_except_shows_prompt(self):
        from src.core.parser import CancelAllExcept

        service, cal = _make_service()
        events = [
            {"summary": "Meeting with Amit", "id": "1"},
            {"summary": "Padel game", "id": "2"},
            {"summary": "Meeting with Shon", "id": "3"},
        ]
        to_cancel = [events[0], events[2]]
        cal.find_events = AsyncMock(return_value=events)

        parsed = CancelAllExcept(date="2026-02-08", exceptions=["Padel game"])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.batch_exclude_events", AsyncMock(return_value=to_cancel)):
            response = await service.process_text("Cancel everything except padel")

        assert isinstance(response, BatchCancelPromptResponse)
        assert len(response.will_cancel) == 2
        assert "Padel game" in response.will_keep
        assert response.pending is not None
        assert len(response.pending.events) == 2


# ---------------------------------------------------------------------------
# process_text — no actions / batch
# ---------------------------------------------------------------------------


class TestProcessTextNoActionAndBatch:
    @pytest.mark.asyncio
    async def test_no_actions(self):
        service, _ = _make_service()

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[])):
            response = await service.process_text("Hello there")

        assert isinstance(response, NoActionResponse)
        assert response.kind == ResponseKind.NO_ACTION

    @pytest.mark.asyncio
    async def test_parser_error(self):
        service, _ = _make_service()

        with patch("src.core.parser.parse_message", AsyncMock(side_effect=Exception("LLM down"))):
            response = await service.process_text("Meeting at 14:00")

        assert isinstance(response, ErrorResponse)
        assert "went wrong" in response.message

    @pytest.mark.asyncio
    async def test_multi_actions_batch_summary(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        actions = [
            ParsedEvent(event="Meeting A", date="2026-02-08", time="10:00"),
            ParsedEvent(event="Meeting B", date="2026-02-08", time="16:00"),
        ]
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Set up two meetings")

        assert isinstance(response, BatchSummaryResponse)
        assert len(response.results) == 2
        assert all(r.success for r in response.results)
        assert "Processed 2 actions" in response.message

    @pytest.mark.asyncio
    async def test_multi_cancel_batch(self):
        from src.core.parser import CancelEvent

        service, cal = _make_service()
        events = [
            {"summary": "Meeting A", "id": "1"},
            {"summary": "Meeting B", "id": "2"},
        ]
        cal.find_events = AsyncMock(return_value=events)
        cal.delete_event = AsyncMock()

        actions = [
            CancelEvent(event_summary="Meeting A", date="2026-02-08"),
            CancelEvent(event_summary="Meeting B", date="2026-02-08"),
        ]

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.parser.batch_match_events", AsyncMock(return_value=events)):
            response = await service.process_text("Cancel both meetings")

        assert isinstance(response, BatchSummaryResponse)
        assert cal.delete_event.call_count == 2
        # find_events called once per date (optimization)
        cal.find_events.assert_called_once_with(target_date="2026-02-08")


# ---------------------------------------------------------------------------
# resolve_conflict
# ---------------------------------------------------------------------------


class TestResolveConflict:
    @pytest.mark.asyncio
    async def test_resolve_suggested(self):
        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting", "date": "2026-02-08",
                "time": "14:00", "duration_minutes": 60, "description": "",
            },
            time="15:00",  # suggested time
        )

        response = await service.resolve_conflict(pending, "suggested")

        assert isinstance(response, SuccessResponse)
        assert "15:00" in response.message
        cal.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_force(self):
        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting", "date": "2026-02-08",
                "time": "14:00", "duration_minutes": 60, "description": "",
            },
            time="15:00",
        )

        response = await service.resolve_conflict(pending, "force")

        assert isinstance(response, SuccessResponse)
        assert "14:00" in response.message
        cal.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_custom_valid(self):
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting", "date": "2026-02-08",
                "time": "14:00", "duration_minutes": 60, "description": "",
            },
        )
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.resolve_conflict(pending, "custom", custom_time="16:30")

        assert isinstance(response, SuccessResponse)
        assert "16:30" in response.message

    @pytest.mark.asyncio
    async def test_resolve_custom_invalid(self):
        service, _ = _make_service()

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting", "date": "2026-02-08",
                "time": "14:00", "duration_minutes": 60, "description": "",
            },
        )

        response = await service.resolve_conflict(pending, "custom", custom_time="not-a-time")

        assert isinstance(response, ErrorResponse)
        assert "Invalid time" in response.message

    @pytest.mark.asyncio
    async def test_resolve_custom_still_conflicts_warns(self):
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting", "date": "2026-02-08",
                "time": "14:00", "duration_minutes": 60, "description": "",
            },
        )
        still_conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Another meeting"}],
        )

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=still_conflict)):
            response = await service.resolve_conflict(pending, "custom", custom_time="15:00")

        assert isinstance(response, SuccessResponse)
        assert "also conflicts" in response.message

    @pytest.mark.asyncio
    async def test_resolve_reschedule_force(self):
        service, cal = _make_service()
        cal.update_event = AsyncMock(return_value={"summary": "My Meeting", "htmlLink": ""})

        pending = PendingEvent(
            pending_type="reschedule",
            event_id="ev1",
            date="2026-02-08",
            time="14:00",
            duration=60,
            summary="My Meeting",
        )

        response = await service.resolve_conflict(pending, "force")

        assert isinstance(response, SuccessResponse)
        assert "rescheduled" in response.message.lower()
        cal.update_event.assert_called_once_with("ev1", "2026-02-08", "14:00")


# ---------------------------------------------------------------------------
# confirm_batch_cancel
# ---------------------------------------------------------------------------


class TestConfirmBatchCancel:
    @pytest.mark.asyncio
    async def test_confirm_deletes_all(self):
        service, cal = _make_service()
        cal.delete_event = AsyncMock()

        pending = PendingBatchCancel(events=[
            {"id": "1", "summary": "Meeting A"},
            {"id": "2", "summary": "Meeting B"},
        ])

        response = await service.confirm_batch_cancel(pending)

        assert isinstance(response, SuccessResponse)
        assert cal.delete_event.call_count == 2
        assert "Meeting A" in response.message
        assert "Meeting B" in response.message

    @pytest.mark.asyncio
    async def test_confirm_partial_failure(self):
        from src.ports.calendar_port import CalendarError

        service, cal = _make_service()
        cal.delete_event = AsyncMock(side_effect=[None, CalendarError("fail")])

        pending = PendingBatchCancel(events=[
            {"id": "1", "summary": "Meeting A"},
            {"id": "2", "summary": "Meeting B"},
        ])

        response = await service.confirm_batch_cancel(pending)

        assert isinstance(response, SuccessResponse)
        assert "Meeting A" in response.message
        assert "Failed to cancel" in response.message


# ---------------------------------------------------------------------------
# get_today_events
# ---------------------------------------------------------------------------


class TestGetTodayEvents:
    @pytest.mark.asyncio
    async def test_with_events(self):
        service, cal = _make_service()
        cal.get_daily_events = AsyncMock(return_value=[
            {"summary": "Standup", "start_time": "2026-02-08T09:00:00", "end_time": "2026-02-08T09:15:00"},
        ])

        response = await service.get_today_events()

        assert isinstance(response, QueryResultResponse)
        assert "Standup" in response.message
        assert len(response.events) == 1

    @pytest.mark.asyncio
    async def test_no_events(self):
        service, cal = _make_service()
        cal.get_daily_events = AsyncMock(return_value=[])

        response = await service.get_today_events()

        assert isinstance(response, QueryResultResponse)
        assert "No events" in response.message

    @pytest.mark.asyncio
    async def test_calendar_error(self):
        from src.ports.calendar_port import CalendarError

        service, cal = _make_service()
        cal.get_daily_events = AsyncMock(side_effect=CalendarError("fail"))

        response = await service.get_today_events()

        assert isinstance(response, ErrorResponse)


# ---------------------------------------------------------------------------
# Chore operations
# ---------------------------------------------------------------------------


class TestChoreOperations:
    @pytest.mark.asyncio
    async def test_delete_chore_success(self):
        from src.data.models import Chore

        service, cal = _make_service()
        cal.delete_event = AsyncMock()

        chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="09:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
            calendar_event_id="gcal123",
        )
        mock_db = MagicMock()
        mock_db.get_chore.return_value = chore
        mock_db.delete_chore.return_value = True

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            response = await service.delete_chore(1)

        assert isinstance(response, SuccessResponse)
        assert "Trash" in response.message
        assert "removed" in response.message
        cal.delete_event.assert_called_once_with("gcal123")

    @pytest.mark.asyncio
    async def test_delete_chore_not_found(self):
        service, _ = _make_service()

        mock_db = MagicMock()
        mock_db.get_chore.return_value = None

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            response = await service.delete_chore(999)

        assert isinstance(response, ErrorResponse)
        assert "not found" in response.message.lower()

    @pytest.mark.asyncio
    async def test_delete_chore_calendar_event_fails(self):
        from src.data.models import Chore
        from src.ports.calendar_port import CalendarError

        service, cal = _make_service()
        cal.delete_event = AsyncMock(side_effect=CalendarError("API error"))

        chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="09:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
            calendar_event_id="gcal123",
        )
        mock_db = MagicMock()
        mock_db.get_chore.return_value = chore
        mock_db.delete_chore.return_value = True

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            response = await service.delete_chore(1)

        assert isinstance(response, SuccessResponse)
        assert "Couldn't remove" in response.message

    def test_list_chores(self):
        from src.data.models import Chore

        service, _ = _make_service()

        chores = [
            Chore(id=1, name="Trash", frequency_days=7, duration_minutes=15,
                  preferred_time_start="09:00", preferred_time_end="21:00",
                  next_due="2026-02-07", assigned_to="Amit"),
        ]
        mock_db = MagicMock()
        mock_db.list_all.return_value = chores

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            result = service.list_chores(active_only=True)

        assert len(result) == 1
        assert result[0].name == "Trash"

    def test_mark_chore_done(self):
        from src.data.models import Chore

        service, _ = _make_service()

        chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="09:00", preferred_time_end="21:00",
            next_due="2026-02-14", assigned_to="Amit",
            last_done="2026-02-07",
        )
        mock_db = MagicMock()
        mock_db.mark_done.return_value = chore

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            result = service.mark_chore_done(1)

        assert result.name == "Trash"
        mock_db.mark_done.assert_called_once_with(1)

    def test_create_chore(self):
        from src.data.models import Chore

        service, _ = _make_service()

        chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="17:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
        )
        mock_db = MagicMock()
        mock_db.add_chore.return_value = chore

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            result = service.create_chore(
                name="Trash", frequency_days=7, assigned_to="Amit",
                duration_minutes=15, preferred_time_start="17:00",
                preferred_time_end="21:00",
            )

        assert result.name == "Trash"
        mock_db.add_chore.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_chore_calendar_event(self):
        from src.data.models import Chore

        service, cal = _make_service()
        cal.add_recurring_event = AsyncMock(return_value={"id": "gcal123", "htmlLink": "https://..."})

        chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="17:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
        )
        slot = {
            "start_date": "2026-02-08", "start_time": "17:00",
            "end_time": "17:15", "frequency_days": 7, "occurrences": 4,
        }

        mock_db = MagicMock()
        with patch("src.data.db.ChoreDB", return_value=mock_db):
            response = await service.create_chore_calendar_event(chore, slot)

        assert isinstance(response, SuccessResponse)
        cal.add_recurring_event.assert_called_once()
        mock_db.set_calendar_event_id.assert_called_once_with(1, "gcal123")

    @pytest.mark.asyncio
    async def test_find_chore_slot(self):
        service, cal = _make_service()

        mock_slot = {
            "start_date": "2026-02-08", "start_time": "17:00",
            "end_time": "17:30", "occurrences": 4, "frequency_days": 7,
        }

        with patch("src.core.chore_scheduler.find_best_slot", AsyncMock(return_value=mock_slot)):
            result = await service.find_chore_slot(
                name="Test", frequency_days=7, duration_minutes=30,
                preferred_start="17:00", preferred_end="21:00", weeks_ahead=4,
            )

        assert result == mock_slot


# ---------------------------------------------------------------------------
# process_text — add guests
# ---------------------------------------------------------------------------


class TestProcessTextAddGuests:
    @pytest.mark.asyncio
    async def test_add_guests_success(self):
        from src.core.parser import AddGuests

        service, cal = _make_service()
        events = [{"summary": "Meeting with Dan", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)
        cal.add_guests = AsyncMock(return_value={"id": "ev1", "summary": "Meeting with Dan"})

        parsed = AddGuests(event_summary="Meeting with Dan", date="2026-02-08", guests=["dan@email.com"])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=events[0])):
            response = await service.process_text("Add dan@email.com to meeting with Dan")

        assert isinstance(response, SuccessResponse)
        assert "dan@email.com" in response.message
        assert "Meeting with Dan" in response.message
        cal.add_guests.assert_called_once_with("ev1", ["dan@email.com"])

    @pytest.mark.asyncio
    async def test_add_guests_no_match(self):
        from src.core.parser import AddGuests

        service, cal = _make_service()
        events = [{"summary": "Padel game", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)

        parsed = AddGuests(event_summary="Meeting with Dan", date="2026-02-08", guests=["dan@email.com"])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=None)):
            response = await service.process_text("Add dan@email.com to meeting with Dan")

        assert isinstance(response, ErrorResponse)
        assert "couldn't match" in response.message.lower()

    @pytest.mark.asyncio
    async def test_add_guests_no_events_on_date(self):
        from src.core.parser import AddGuests

        service, cal = _make_service()
        cal.find_events = AsyncMock(return_value=[])

        parsed = AddGuests(event_summary="Meeting", date="2026-02-08", guests=["dan@email.com"])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("Add dan@email.com to meeting")

        assert isinstance(response, ErrorResponse)
        assert "no events" in response.message.lower()

    @pytest.mark.asyncio
    async def test_add_guests_calendar_error(self):
        from src.core.parser import AddGuests
        from src.ports.calendar_port import CalendarError

        service, cal = _make_service()
        events = [{"summary": "Meeting", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)
        cal.add_guests = AsyncMock(side_effect=CalendarError("API error"))

        parsed = AddGuests(event_summary="Meeting", date="2026-02-08", guests=["dan@email.com"])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.parser.match_event", AsyncMock(return_value=events[0])):
            response = await service.process_text("Add dan@email.com to meeting")

        assert isinstance(response, ErrorResponse)
        assert "couldn't add guests" in response.message.lower()

    @pytest.mark.asyncio
    async def test_add_guests_batch(self):
        from src.core.parser import AddGuests, ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        events = [{"summary": "Meeting", "id": "ev1"}]
        cal.find_events = AsyncMock(return_value=events)
        cal.add_guests = AsyncMock(return_value={"id": "ev1"})
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        actions = [
            ParsedEvent(event="Lunch", date="2026-02-08", time="12:00"),
            AddGuests(event_summary="Meeting", date="2026-02-08", guests=["dan@email.com"]),
        ]
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)), \
             patch("src.core.parser.match_event", AsyncMock(return_value=events[0])):
            response = await service.process_text("Create lunch at 12 and add dan@email.com to meeting")

        assert isinstance(response, BatchSummaryResponse)
        assert len(response.results) == 2
        assert response.results[0].action_type == "create"
        assert response.results[1].action_type == "add_guests"
        assert all(r.success for r in response.results)


# ---------------------------------------------------------------------------
# process_text — slot suggestions (missing time)
# ---------------------------------------------------------------------------


class TestProcessTextSlotSuggestion:
    @pytest.mark.asyncio
    async def test_empty_time_returns_slot_suggestion(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import FreeSlotResult

        service, cal = _make_service()
        parsed = ParsedEvent(event="Meeting with Shon", date="2026-02-08", time="")
        free_result = FreeSlotResult(
            suggested=["09:00", "12:00", "16:00"],
            all_available=["09:00", "09:30", "10:00", "12:00", "14:00", "16:00"],
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.get_free_slots", AsyncMock(return_value=free_result)):
            response = await service.process_text("Meeting with Shon today")

        assert isinstance(response, SlotSuggestionResponse)
        assert response.kind == ResponseKind.SLOT_SUGGESTION
        assert len(response.slots) == 3
        assert response.slots[0].time == "09:00"
        assert response.pending is not None
        assert response.pending.pending_type == "create"
        assert "Meeting with Shon" in response.message
        assert response.is_flexible is True
        assert len(response.all_free_slots) == 6

    @pytest.mark.asyncio
    async def test_with_time_still_creates_normally(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Meeting at 14:00")

        assert isinstance(response, SuccessResponse)
        cal.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_slots_available_returns_error(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import FreeSlotResult

        service, cal = _make_service()
        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="")
        empty_result = FreeSlotResult(suggested=[], all_available=[])

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.get_free_slots", AsyncMock(return_value=empty_result)):
            response = await service.process_text("Meeting today")

        assert isinstance(response, ErrorResponse)
        assert "No available slots" in response.message

    @pytest.mark.asyncio
    async def test_batch_with_missing_time_fails_that_action(self):
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        actions = [
            ParsedEvent(event="Meeting A", date="2026-02-08", time="10:00"),
            ParsedEvent(event="Meeting B", date="2026-02-08", time=""),
        ]
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Two meetings")

        assert isinstance(response, BatchSummaryResponse)
        assert response.results[0].success is True
        assert response.results[1].success is False
        assert "No time specified" in response.results[1].error_message


# ---------------------------------------------------------------------------
# select_slot
# ---------------------------------------------------------------------------


class TestSelectSlot:
    @pytest.mark.asyncio
    async def test_select_slot_creates_event(self):
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})
        no_conflict = ConflictResult(has_conflict=False)

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting with Shon",
                "date": "2026-02-08", "time": "", "duration_minutes": 60,
                "description": "", "guests": [],
            },
        )

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.select_slot(pending, "09:00")

        assert isinstance(response, SuccessResponse)
        assert "09:00" in response.message
        assert "Meeting with Shon" in response.message
        cal.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_select_slot_calendar_error(self):
        from src.ports.calendar_port import CalendarError
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(side_effect=CalendarError("API error"))
        no_conflict = ConflictResult(has_conflict=False)

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting",
                "date": "2026-02-08", "time": "", "duration_minutes": 60,
                "description": "", "guests": [],
            },
        )

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.select_slot(pending, "09:00")

        assert isinstance(response, ErrorResponse)

    @pytest.mark.asyncio
    async def test_select_slot_conflict_detected(self):
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Existing meeting"}],
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting",
                "date": "2026-02-08", "time": "", "duration_minutes": 60,
                "description": "", "guests": [],
            },
        )

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=conflict)):
            response = await service.select_slot(pending, "14:00")

        assert isinstance(response, ErrorResponse)
        assert "conflicts with" in response.message
        assert "Existing meeting" in response.message

    @pytest.mark.asyncio
    async def test_select_slot_unlisted_time_works_if_free(self):
        """User types a time not in the suggested list — should work if calendar is free."""
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service()
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})
        no_conflict = ConflictResult(has_conflict=False)

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={
                "intent": "create", "event": "Meeting",
                "date": "2026-02-08", "time": "", "duration_minutes": 60,
                "description": "", "guests": [],
            },
        )

        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.select_slot(pending, "14:15")

        assert isinstance(response, SuccessResponse)
        assert "14:15" in response.message


# ---------------------------------------------------------------------------
# Contact resolution
# ---------------------------------------------------------------------------


def _make_service_with_contacts(calendar=None, contact_db=None):
    """Create an ActionService with a mock calendar and optional contact DB."""
    cal = calendar or MagicMock()
    return ActionService(cal, contact_db=contact_db), cal


class TestContactResolution:
    @pytest.mark.asyncio
    async def test_known_contact_auto_resolves(self, contact_db):
        """Event with a known contact should auto-resolve to guest email."""
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        contact_db.add_contact("Yahav", "yahav@gmail.com")

        service, cal = _make_service_with_contacts(contact_db=contact_db)
        cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})

        parsed = ParsedEvent(
            event="Meeting with Yahav", date="2026-02-08", time="14:00",
            mentioned_contacts=["Yahav"],
        )
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Meeting with Yahav at 14:00")

        assert isinstance(response, SuccessResponse)
        # Verify the calendar was called with guests
        call_args = cal.add_event.call_args[0][0]
        assert "yahav@gmail.com" in call_args.guests
        assert call_args.mentioned_contacts == []

    @pytest.mark.asyncio
    async def test_unknown_contact_returns_prompt(self, contact_db):
        """Event with unknown contact should return ContactPromptResponse."""
        from src.core.parser import ParsedEvent

        service, cal = _make_service_with_contacts(contact_db=contact_db)

        parsed = ParsedEvent(
            event="Meeting with Yahav", date="2026-02-08", time="14:00",
            mentioned_contacts=["Yahav"],
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("Meeting with Yahav at 14:00")

        assert isinstance(response, ContactPromptResponse)
        assert response.kind == ResponseKind.CONTACT_PROMPT
        assert response.contact_name == "Yahav"
        assert response.pending is not None
        assert "Yahav" in response.pending.unresolved_contacts

    @pytest.mark.asyncio
    async def test_resolve_contact_saves_and_retries(self, contact_db):
        """resolve_contact should save to DB and re-execute the action."""
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service_with_contacts(contact_db=contact_db)
        cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})

        pending = PendingContactResolution(
            action_type="create",
            parsed_action_json={
                "intent": "create", "event": "Meeting with Yahav",
                "date": "2026-02-08", "time": "14:00",
                "duration_minutes": 60, "description": "",
                "guests": [], "mentioned_contacts": ["Yahav"],
            },
            resolved_contacts={},
            unresolved_contacts=["Yahav"],
            current_asking="Yahav",
        )

        no_conflict = ConflictResult(has_conflict=False)
        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.resolve_contact(pending, "yahav@gmail.com")

        assert isinstance(response, SuccessResponse)
        # Verify contact was saved to DB
        found = contact_db.find_by_name("Yahav")
        assert found is not None
        assert found.email == "yahav@gmail.com"

    @pytest.mark.asyncio
    async def test_resolve_contact_invalid_email_reprompts(self, contact_db):
        """Invalid email should re-prompt for the same contact."""
        service, cal = _make_service_with_contacts(contact_db=contact_db)

        pending = PendingContactResolution(
            action_type="create",
            parsed_action_json={
                "intent": "create", "event": "Meeting",
                "date": "2026-02-08", "time": "14:00",
                "duration_minutes": 60, "description": "",
                "guests": [], "mentioned_contacts": ["Yahav"],
            },
            resolved_contacts={},
            unresolved_contacts=["Yahav"],
            current_asking="Yahav",
        )

        response = await service.resolve_contact(pending, "not-an-email")

        assert isinstance(response, ContactPromptResponse)
        assert "doesn't look like a valid email" in response.message
        assert response.contact_name == "Yahav"

    @pytest.mark.asyncio
    async def test_multiple_unknown_contacts_one_at_a_time(self, contact_db):
        """Multiple unknown contacts should be asked one at a time."""
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service_with_contacts(contact_db=contact_db)
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        parsed = ParsedEvent(
            event="Meeting", date="2026-02-08", time="14:00",
            mentioned_contacts=["Yahav", "Dan"],
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("Meeting with Yahav and Dan")

        # First prompt for Yahav
        assert isinstance(response, ContactPromptResponse)
        assert response.contact_name == "Yahav"

        # Resolve Yahav, should now prompt for Dan
        no_conflict = ConflictResult(has_conflict=False)
        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response2 = await service.resolve_contact(response.pending, "yahav@gmail.com")

        assert isinstance(response2, ContactPromptResponse)
        assert response2.contact_name == "Dan"

        # Resolve Dan, should create the event
        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response3 = await service.resolve_contact(response2.pending, "dan@example.com")

        assert isinstance(response3, SuccessResponse)

    @pytest.mark.asyncio
    async def test_batch_mode_skips_unresolved(self, contact_db):
        """In batch mode, unresolved contacts should fail with error message."""
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        service, cal = _make_service_with_contacts(contact_db=contact_db)

        actions = [
            ParsedEvent(
                event="Meeting with Yahav", date="2026-02-08", time="14:00",
                mentioned_contacts=["Yahav"],
            ),
            ParsedEvent(
                event="Lunch", date="2026-02-08", time="12:00",
            ),
        ]
        no_conflict = ConflictResult(has_conflict=False)
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Two things")

        assert isinstance(response, BatchSummaryResponse)
        # First action should fail (unknown Yahav)
        assert not response.results[0].success
        assert "Unknown contact" in response.results[0].error_message
        # Second action (no contacts) should succeed
        assert response.results[1].success

    @pytest.mark.asyncio
    async def test_batch_mode_auto_resolves_known(self, contact_db):
        """In batch mode, known contacts should auto-resolve."""
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        contact_db.add_contact("Yahav", "yahav@gmail.com")
        service, cal = _make_service_with_contacts(contact_db=contact_db)
        cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        actions = [
            ParsedEvent(
                event="Meeting with Yahav", date="2026-02-08", time="14:00",
                mentioned_contacts=["Yahav"],
            ),
            ParsedEvent(
                event="Lunch", date="2026-02-08", time="12:00",
            ),
        ]
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=actions)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            response = await service.process_text("Two things")

        assert isinstance(response, BatchSummaryResponse)
        assert response.results[0].success
        assert response.results[1].success

    @pytest.mark.asyncio
    async def test_no_contact_db_treats_all_as_unresolved(self):
        """With no contact DB, all mentioned contacts are unresolved."""
        from src.core.parser import ParsedEvent

        service, cal = _make_service(MagicMock())

        parsed = ParsedEvent(
            event="Meeting", date="2026-02-08", time="14:00",
            mentioned_contacts=["Yahav"],
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("Meeting with Yahav")

        assert isinstance(response, ContactPromptResponse)
        assert response.contact_name == "Yahav"
