"""Tests for src.bot.telegram_bot — Telegram bot handlers.

Tests the Telegram rendering layer, conversation flow logic, command handlers,
and authorization. Business logic is tested in test_action_service.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bot.telegram_bot import (
    _parse_time_pref,
    _clear_chore_data,
    CHORE_NAME,
    CHORE_FREQ,
    CHORE_DURATION,
    CHORE_TIME_PREF,
    CHORE_WEEKS,
    CHORE_CONFIRM,
)
from src.core.action_service import (
    ActionService,
    BatchCancelPromptResponse,
    BatchSummaryResponse,
    ConflictOption,
    ConflictPromptResponse,
    ContactPromptResponse,
    ErrorResponse,
    EventInfo,
    NoActionResponse,
    PendingBatchCancel,
    PendingContactResolution,
    PendingEvent,
    QueryResultResponse,
    ResponseKind,
    SuccessResponse,
    ActionResult,
)


# ---------------------------------------------------------------------------
# Tests for _parse_time_pref
# ---------------------------------------------------------------------------


class TestParseTimePref:
    def test_mornings(self):
        assert _parse_time_pref("mornings") == ("06:00", "12:00")
        assert _parse_time_pref("Morning") == ("06:00", "12:00")

    def test_afternoons(self):
        assert _parse_time_pref("afternoons") == ("12:00", "17:00")

    def test_evenings(self):
        assert _parse_time_pref("Evenings") == ("17:00", "21:00")

    def test_explicit_range(self):
        assert _parse_time_pref("17:00-20:00") == ("17:00", "20:00")

    def test_explicit_range_with_spaces(self):
        assert _parse_time_pref(" 08:00 - 12:00 ") == ("08:00", "12:00")

    def test_invalid_returns_none(self):
        assert _parse_time_pref("whenever") is None
        assert _parse_time_pref("") is None
        assert _parse_time_pref("abc-def") is None


# ---------------------------------------------------------------------------
# Tests for _clear_chore_data
# ---------------------------------------------------------------------------


class TestClearChoreData:
    def test_clears_all_keys(self):
        context = MagicMock()
        context.user_data = {
            "chore_name": "Test",
            "chore_freq": 7,
            "chore_times_per_week": 1,
            "chore_duration": 30,
            "chore_assigned": "Me",
            "chore_time_start": "17:00",
            "chore_time_end": "21:00",
            "chore_weeks": 4,
            "chore_slot": {},
            "unrelated_key": "keep",
        }
        _clear_chore_data(context)
        assert "chore_name" not in context.user_data
        assert "unrelated_key" in context.user_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(text, user_id=12345, first_name="Amit"):
    """Create a mock Update with a text message from an authorized user."""
    update = MagicMock()
    update.message.text = text
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.message.reply_text = AsyncMock()
    return update


def _make_service(calendar=None):
    """Create a mock ActionService."""
    service = MagicMock(spec=ActionService)
    return service


def _make_context(calendar=None, service=None):
    """Create a mock context with user_data dict, bot_data with calendar and action_service."""
    context = MagicMock()
    context.user_data = {}
    mock_cal = calendar or MagicMock()
    mock_service = service or _make_service(mock_cal)
    context.bot_data = {
        "calendar": mock_cal,
        "action_service": mock_service,
    }
    return context


# ---------------------------------------------------------------------------
# Tests for addchore conversation handlers
# ---------------------------------------------------------------------------


class TestAddchoreNameHandler:
    @pytest.mark.asyncio
    async def test_stores_name_and_advances(self):
        from src.bot.telegram_bot import addchore_name

        update = _make_update("Clean the kitchen")
        context = _make_context()
        result = await addchore_name(update, context)
        assert context.user_data["chore_name"] == "Clean the kitchen"
        assert result == CHORE_FREQ


class TestAddchoreFreqHandler:
    @pytest.mark.asyncio
    async def test_valid_frequency(self):
        from src.bot.telegram_bot import addchore_freq

        update = _make_update("3")
        context = _make_context()
        result = await addchore_freq(update, context)
        # 3 times per week → every 2 days
        assert context.user_data["chore_freq"] == 2
        assert context.user_data["chore_times_per_week"] == 3
        assert result == CHORE_DURATION

    @pytest.mark.asyncio
    async def test_invalid_frequency_retries(self):
        from src.bot.telegram_bot import addchore_freq

        update = _make_update("abc")
        context = _make_context()
        result = await addchore_freq(update, context)
        assert result == CHORE_FREQ

    @pytest.mark.asyncio
    async def test_zero_frequency_retries(self):
        from src.bot.telegram_bot import addchore_freq

        update = _make_update("0")
        context = _make_context()
        result = await addchore_freq(update, context)
        assert result == CHORE_FREQ


class TestAddchoreDurationHandler:
    @pytest.mark.asyncio
    async def test_valid_duration(self):
        from src.bot.telegram_bot import addchore_duration

        update = _make_update("45")
        context = _make_context()
        result = await addchore_duration(update, context)
        assert context.user_data["chore_duration"] == 45
        assert context.user_data["chore_assigned"] == "Amit"
        assert result == CHORE_TIME_PREF

    @pytest.mark.asyncio
    async def test_invalid_duration_retries(self):
        from src.bot.telegram_bot import addchore_duration

        update = _make_update("not a number")
        context = _make_context()
        result = await addchore_duration(update, context)
        assert result == CHORE_DURATION


class TestAddchoreTimePrefHandler:
    @pytest.mark.asyncio
    async def test_valid_time_pref(self):
        from src.bot.telegram_bot import addchore_time_pref

        update = _make_update("Evenings")
        context = _make_context()
        result = await addchore_time_pref(update, context)
        assert context.user_data["chore_time_start"] == "17:00"
        assert context.user_data["chore_time_end"] == "21:00"
        assert result == CHORE_WEEKS

    @pytest.mark.asyncio
    async def test_invalid_time_pref_retries(self):
        from src.bot.telegram_bot import addchore_time_pref

        update = _make_update("whenever")
        context = _make_context()
        result = await addchore_time_pref(update, context)
        assert result == CHORE_TIME_PREF


class TestAddchoreWeeksHandler:
    @pytest.mark.asyncio
    async def test_valid_weeks_finds_slot(self):
        from src.bot.telegram_bot import addchore_weeks

        mock_slot = {
            "start_date": "2026-02-08",
            "start_time": "17:00",
            "end_time": "17:30",
            "occurrences": 4,
            "frequency_days": 7,
        }

        mock_service = _make_service()
        mock_service.find_chore_slot = AsyncMock(return_value=mock_slot)

        update = _make_update("4")
        context = _make_context(service=mock_service)
        context.user_data.update({
            "chore_name": "Test",
            "chore_freq": 7,
            "chore_duration": 30,
            "chore_time_start": "17:00",
            "chore_time_end": "21:00",
        })

        result = await addchore_weeks(update, context)
        assert result == CHORE_CONFIRM
        assert context.user_data["chore_slot"] == mock_slot

    @pytest.mark.asyncio
    async def test_invalid_weeks_retries(self):
        from src.bot.telegram_bot import addchore_weeks

        update = _make_update("abc")
        context = _make_context()
        result = await addchore_weeks(update, context)
        assert result == CHORE_WEEKS


class TestAddchoreConfirmHandler:
    @pytest.mark.asyncio
    async def test_confirm_yes_creates_chore_and_event(self):
        from src.bot.telegram_bot import addchore_confirm
        from telegram.ext import ConversationHandler

        mock_chore = MagicMock()
        mock_chore.id = 1

        mock_service = _make_service()
        mock_service.create_chore.return_value = mock_chore
        mock_service.create_chore_calendar_event = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="",
                event=EventInfo(summary="Test", date="2026-02-08", time="17:00", link="https://..."),
            )
        )

        update = _make_update("yes")
        context = _make_context(service=mock_service)
        context.user_data.update({
            "chore_name": "Test chore",
            "chore_freq": 7,
            "chore_duration": 30,
            "chore_assigned": "Amit",
            "chore_time_start": "17:00",
            "chore_time_end": "21:00",
            "chore_times_per_week": 1,
            "chore_slot": {
                "start_date": "2026-02-08",
                "start_time": "17:00",
                "end_time": "17:30",
                "frequency_days": 7,
                "occurrences": 4,
            },
        })

        result = await addchore_confirm(update, context)

        assert result == ConversationHandler.END
        mock_service.create_chore.assert_called_once()
        mock_service.create_chore_calendar_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_no_cancels(self):
        from src.bot.telegram_bot import addchore_confirm
        from telegram.ext import ConversationHandler

        update = _make_update("no")
        context = _make_context()
        context.user_data.update({"chore_name": "Test"})
        result = await addchore_confirm(update, context)
        assert result == ConversationHandler.END


# ---------------------------------------------------------------------------
# Tests for authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    @pytest.mark.asyncio
    async def test_unauthorized_user_is_ignored(self):
        from src.bot.telegram_bot import cmd_start

        update = MagicMock()
        update.effective_user.id = 99999  # not in ALLOWED_USER_IDS
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await cmd_start(update, context)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_user_gets_response(self):
        from src.bot.telegram_bot import cmd_start

        update = MagicMock()
        update.effective_user.id = 12345  # matches ALLOWED_USER_IDS in conftest
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await cmd_start(update, context)
        update.message.reply_text.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for /chores command
# ---------------------------------------------------------------------------


class TestChoresCommand:
    @pytest.mark.asyncio
    async def test_no_active_chores(self):
        from src.bot.telegram_bot import cmd_chores

        mock_service = _make_service()
        mock_service.list_chores.return_value = []

        update = _make_update("/chores")
        context = _make_context(service=mock_service)

        await cmd_chores(update, context)
        update.message.reply_text.assert_called_with("No active chores.")

    @pytest.mark.asyncio
    async def test_lists_active_chores(self):
        from src.bot.telegram_bot import cmd_chores
        from src.data.models import Chore

        mock_chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="09:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
        )
        mock_service = _make_service()
        mock_service.list_chores.return_value = [mock_chore]

        update = _make_update("/chores")
        context = _make_context(service=mock_service)

        await cmd_chores(update, context)
        call_text = update.message.reply_text.call_args[0][0]
        assert "Trash" in call_text


# ---------------------------------------------------------------------------
# Tests for /deletechore flow
# ---------------------------------------------------------------------------


class TestDeleteChoreFlow:
    @pytest.mark.asyncio
    async def test_shows_chore_buttons(self):
        from src.bot.telegram_bot import cmd_deletechore
        from src.data.models import Chore

        chores = [
            Chore(id=1, name="Trash", frequency_days=7, duration_minutes=15,
                  preferred_time_start="09:00", preferred_time_end="21:00",
                  next_due="2026-02-07", assigned_to="Amit"),
            Chore(id=2, name="Vacuum", frequency_days=3, duration_minutes=30,
                  preferred_time_start="17:00", preferred_time_end="21:00",
                  next_due="2026-02-07", assigned_to="Amit"),
        ]
        mock_service = _make_service()
        mock_service.list_chores.return_value = chores

        update = _make_update("/deletechore")
        context = _make_context(service=mock_service)

        await cmd_deletechore(update, context)

        call_kwargs = update.message.reply_text.call_args
        assert "Which chore" in call_kwargs[0][0]
        # Verify inline keyboard was passed
        assert call_kwargs[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_no_chores_to_delete(self):
        from src.bot.telegram_bot import cmd_deletechore

        mock_service = _make_service()
        mock_service.list_chores.return_value = []

        update = _make_update("/deletechore")
        context = _make_context(service=mock_service)

        await cmd_deletechore(update, context)
        update.message.reply_text.assert_called_with("No active chores to delete.")


# ---------------------------------------------------------------------------
# Tests for _render_response
# ---------------------------------------------------------------------------


class TestRenderResponse:
    @pytest.mark.asyncio
    async def test_render_success_with_link(self):
        from src.bot.telegram_bot import _render_response

        response = SuccessResponse(
            kind=ResponseKind.SUCCESS,
            message="Event created!",
            event=EventInfo(summary="Meeting", date="2026-02-08", time="14:00", link="https://cal/1"),
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert "Event created!" in call_text
        assert "Open in Google Calendar" in call_text

    @pytest.mark.asyncio
    async def test_render_success_without_link(self):
        from src.bot.telegram_bot import _render_response

        response = SuccessResponse(
            kind=ResponseKind.SUCCESS,
            message="Event canceled!",
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert "Event canceled!" in call_text
        assert "Calendar" not in call_text

    @pytest.mark.asyncio
    async def test_render_conflict_prompt_stores_pending(self):
        from src.bot.telegram_bot import _render_response

        pending = PendingEvent(pending_type="create")
        response = ConflictPromptResponse(
            kind=ResponseKind.CONFLICT_PROMPT,
            message="Conflict detected!",
            options=[
                ConflictOption(key="suggested", label="Use 15:00", time="15:00"),
                ConflictOption(key="force", label="Force 14:00", time="14:00"),
                ConflictOption(key="custom", label="Enter custom time"),
                ConflictOption(key="cancel", label="Cancel"),
            ],
            conflicting_summaries=["Existing meeting"],
            pending=pending,
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)

        # Should store pending in user_data
        assert context.user_data["pending_event"] is pending
        # Should send message with keyboard
        call_kwargs = update.message.reply_text.call_args
        assert "Conflict" in call_kwargs[0][0]
        assert call_kwargs[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_render_batch_cancel_prompt_stores_pending(self):
        from src.bot.telegram_bot import _render_response

        pending = PendingBatchCancel(events=[{"id": "1", "summary": "Test"}])
        response = BatchCancelPromptResponse(
            kind=ResponseKind.BATCH_CANCEL_PROMPT,
            message="Confirm cancel?",
            will_cancel=["Test"],
            will_keep=["Padel"],
            pending=pending,
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)

        assert context.user_data["pending_batch_cancel"] is pending
        call_kwargs = update.message.reply_text.call_args
        assert call_kwargs[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_render_error(self):
        from src.bot.telegram_bot import _render_response

        response = ErrorResponse(kind=ResponseKind.ERROR, message="Something went wrong")
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)
        update.message.reply_text.assert_called_with("Something went wrong")

    @pytest.mark.asyncio
    async def test_render_no_action(self):
        from src.bot.telegram_bot import _render_response

        response = NoActionResponse(kind=ResponseKind.NO_ACTION, message="No actions found")
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)
        update.message.reply_text.assert_called_with("No actions found")

    @pytest.mark.asyncio
    async def test_render_query_result(self):
        from src.bot.telegram_bot import _render_response

        response = QueryResultResponse(
            kind=ResponseKind.QUERY_RESULT,
            message="*Events on 2026-02-08:*\nMeeting at 10:00",
            date="2026-02-08",
            events=[],
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)
        call_kwargs = update.message.reply_text.call_args
        assert "Events" in call_kwargs[0][0]
        assert call_kwargs[1]["parse_mode"] == "Markdown"

    @pytest.mark.asyncio
    async def test_render_batch_summary(self):
        from src.bot.telegram_bot import _render_response

        response = BatchSummaryResponse(
            kind=ResponseKind.BATCH_SUMMARY,
            message="*Processed 2 actions:*\nOK",
            results=[],
        )
        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)
        call_kwargs = update.message.reply_text.call_args
        assert "Processed 2 actions" in call_kwargs[0][0]


# ---------------------------------------------------------------------------
# Tests for _process_text delegation
# ---------------------------------------------------------------------------


class TestProcessTextDelegation:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_renders(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.process_text = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created!",
            )
        )

        update = _make_update("Meeting at 14:00")
        context = _make_context(service=mock_service)

        await _process_text("Meeting at 14:00", update, context)

        mock_service.process_text.assert_called_once_with("Meeting at 14:00")
        update.message.reply_text.assert_called()

    @pytest.mark.asyncio
    async def test_intercepts_custom_time(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created at 16:30",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
        )

        update = _make_update("16:30")
        context = _make_context(service=mock_service)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = pending

        await _process_text("16:30", update, context)

        mock_service.resolve_conflict.assert_called_once_with(pending, "custom", custom_time="16:30")
        assert "awaiting_custom_time" not in context.user_data


# ---------------------------------------------------------------------------
# Tests for conflict resolution callback
# ---------------------------------------------------------------------------


class TestConflictCallbackHandler:
    def _make_callback_update(self, callback_data, user_id=12345):
        update = MagicMock()
        update.callback_query.data = callback_data
        update.callback_query.from_user.id = user_id
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        return update

    @pytest.mark.asyncio
    async def test_callback_suggested(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created at 15:00",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
            time="15:00",
        )

        update = self._make_callback_update("conflict:suggested")
        context = _make_context(service=mock_service)
        context.user_data["pending_event"] = pending

        await _handle_conflict_callback(update, context)

        mock_service.resolve_conflict.assert_called_once_with(pending, "suggested")
        assert "pending_event" not in context.user_data

    @pytest.mark.asyncio
    async def test_callback_force(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created at 14:00",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
        )

        update = self._make_callback_update("conflict:force")
        context = _make_context(service=mock_service)
        context.user_data["pending_event"] = pending

        await _handle_conflict_callback(update, context)

        mock_service.resolve_conflict.assert_called_once_with(pending, "force")
        assert "pending_event" not in context.user_data

    @pytest.mark.asyncio
    async def test_callback_custom_prompts_user(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        pending = PendingEvent(pending_type="create")

        update = self._make_callback_update("conflict:custom")
        context = _make_context()
        context.user_data["pending_event"] = pending

        await _handle_conflict_callback(update, context)

        assert context.user_data.get("awaiting_custom_time") is True
        update.callback_query.edit_message_text.assert_called_once()
        assert "HH:MM" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_callback_cancel(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        pending = PendingEvent(pending_type="create")

        update = self._make_callback_update("conflict:cancel")
        context = _make_context()
        context.user_data["pending_event"] = pending

        await _handle_conflict_callback(update, context)

        assert "pending_event" not in context.user_data
        update.callback_query.edit_message_text.assert_called_with("Event creation cancelled.")

    @pytest.mark.asyncio
    async def test_callback_no_pending_event(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        update = self._make_callback_update("conflict:force")
        context = _make_context()
        # No pending_event set

        await _handle_conflict_callback(update, context)

        update.callback_query.edit_message_text.assert_called_with(
            "No pending event found. Please try again."
        )

    @pytest.mark.asyncio
    async def test_callback_reschedule_force(self):
        from src.bot.telegram_bot import _handle_conflict_callback

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event rescheduled to 14:00",
            )
        )

        pending = PendingEvent(
            pending_type="reschedule",
            event_id="ev1",
            date="2026-02-08",
            time="14:00",
            duration=60,
            summary="My Meeting",
        )

        update = self._make_callback_update("conflict:force")
        context = _make_context(service=mock_service)
        context.user_data["pending_event"] = pending

        await _handle_conflict_callback(update, context)

        mock_service.resolve_conflict.assert_called_once_with(pending, "force")
        assert "pending_event" not in context.user_data


# ---------------------------------------------------------------------------
# Tests for custom time handler
# ---------------------------------------------------------------------------


class TestCustomTimeHandler:
    @pytest.mark.asyncio
    async def test_valid_custom_time_creates_event(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created at 16:30",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
        )

        update = _make_update("16:30")
        context = _make_context(service=mock_service)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = pending

        await _process_text("16:30", update, context)

        mock_service.resolve_conflict.assert_called_once_with(pending, "custom", custom_time="16:30")
        assert "pending_event" not in context.user_data
        assert "awaiting_custom_time" not in context.user_data

    @pytest.mark.asyncio
    async def test_invalid_format_cancels(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Invalid time format. Please use HH:MM (e.g. 15:30). Event cancelled.",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
        )

        update = _make_update("not-a-time")
        context = _make_context(service=mock_service)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = pending

        await _process_text("not-a-time", update, context)

        assert "pending_event" not in context.user_data
        assert "Invalid time" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_custom_time_with_still_conflicting_warns_and_proceeds(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.resolve_conflict = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="\u26a0\ufe0f Note: 15:00 also conflicts with: Another meeting. Proceeding anyway.\nEvent created at 15:00",
            )
        )

        pending = PendingEvent(
            pending_type="create",
            parsed_event_json={"intent": "create", "event": "M", "date": "2026-02-08", "time": "14:00", "duration_minutes": 60, "description": ""},
        )

        update = _make_update("15:00")
        context = _make_context(service=mock_service)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = pending

        await _process_text("15:00", update, context)

        # Should render the success with warning
        calls = update.message.reply_text.call_args_list
        assert any("also conflicts" in str(c) for c in calls)


# ---------------------------------------------------------------------------
# Tests for batch cancel callback
# ---------------------------------------------------------------------------


class TestBatchCancelCallback:
    @pytest.mark.asyncio
    async def test_batch_cancel_confirm_callback(self):
        from src.bot.telegram_bot import _handle_batch_cancel_callback

        mock_service = _make_service()
        mock_service.confirm_batch_cancel = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="\u2705 Canceled: *Meeting A*\n\u2705 Canceled: *Meeting B*",
            )
        )

        pending = PendingBatchCancel(events=[
            {"id": "1", "summary": "Meeting A"},
            {"id": "3", "summary": "Meeting B"},
        ])

        update = MagicMock()
        update.callback_query.data = "batchcancel:confirm"
        update.callback_query.from_user.id = 12345
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        context = _make_context(service=mock_service)
        context.user_data["pending_batch_cancel"] = pending

        await _handle_batch_cancel_callback(update, context)

        mock_service.confirm_batch_cancel.assert_called_once_with(pending)
        assert "pending_batch_cancel" not in context.user_data

    @pytest.mark.asyncio
    async def test_batch_cancel_abort_callback(self):
        from src.bot.telegram_bot import _handle_batch_cancel_callback

        pending = PendingBatchCancel(events=[
            {"id": "1", "summary": "Meeting A"},
        ])

        update = MagicMock()
        update.callback_query.data = "batchcancel:abort"
        update.callback_query.from_user.id = 12345
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        context = _make_context()
        context.user_data["pending_batch_cancel"] = pending

        await _handle_batch_cancel_callback(update, context)

        update.callback_query.edit_message_text.assert_called_with("Batch cancel aborted.")
        assert "pending_batch_cancel" not in context.user_data


# ---------------------------------------------------------------------------
# Tests for contact email resolution
# ---------------------------------------------------------------------------


class TestContactEmailResolution:
    @pytest.mark.asyncio
    async def test_render_contact_prompt_stores_pending(self):
        from src.bot.telegram_bot import _render_response

        pending = PendingContactResolution(
            action_type="create",
            parsed_action_json={},
            resolved_contacts={},
            unresolved_contacts=["Yahav"],
            current_asking="Yahav",
        )
        response = ContactPromptResponse(
            kind=ResponseKind.CONTACT_PROMPT,
            message="I don't have an email for *Yahav*. What's their email?",
            contact_name="Yahav",
            pending=pending,
        )

        update = _make_update("test")
        context = _make_context()

        await _render_response(response, update, context)

        assert context.user_data["pending_contact"] is pending
        assert context.user_data["awaiting_contact_email"] is True
        call_text = update.message.reply_text.call_args[0][0]
        assert "Yahav" in call_text

    @pytest.mark.asyncio
    async def test_process_text_intercepts_contact_email(self):
        from src.bot.telegram_bot import _process_text

        mock_service = _make_service()
        mock_service.resolve_contact = AsyncMock(
            return_value=SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="Event created!",
            )
        )

        pending = PendingContactResolution(
            action_type="create",
            parsed_action_json={},
            resolved_contacts={},
            unresolved_contacts=["Yahav"],
            current_asking="Yahav",
        )

        update = _make_update("yahav@gmail.com")
        context = _make_context(service=mock_service)
        context.user_data["awaiting_contact_email"] = True
        context.user_data["pending_contact"] = pending

        await _process_text("yahav@gmail.com", update, context)

        mock_service.resolve_contact.assert_called_once_with(pending, "yahav@gmail.com")
        assert "awaiting_contact_email" not in context.user_data
        assert "pending_contact" not in context.user_data

    @pytest.mark.asyncio
    async def test_handle_contact_email_chained_prompts(self):
        from src.bot.telegram_bot import _handle_contact_email

        pending_initial = PendingContactResolution(
            action_type="create",
            parsed_action_json={},
            resolved_contacts={},
            unresolved_contacts=["Yahav", "Dan"],
            current_asking="Yahav",
        )

        pending_next = PendingContactResolution(
            action_type="create",
            parsed_action_json={},
            resolved_contacts={"Yahav": "yahav@gmail.com"},
            unresolved_contacts=["Dan"],
            current_asking="Dan",
        )

        mock_service = _make_service()
        mock_service.resolve_contact = AsyncMock(
            return_value=ContactPromptResponse(
                kind=ResponseKind.CONTACT_PROMPT,
                message="I don't have an email for *Dan*. What's their email?",
                contact_name="Dan",
                pending=pending_next,
            )
        )

        update = _make_update("yahav@gmail.com")
        context = _make_context(service=mock_service)
        context.user_data["pending_contact"] = pending_initial

        await _handle_contact_email("yahav@gmail.com", update, context)

        # Should stay in the contact email flow
        assert context.user_data["awaiting_contact_email"] is True
        assert context.user_data["pending_contact"] is pending_next
        call_text = update.message.reply_text.call_args[0][0]
        assert "Dan" in call_text

    @pytest.mark.asyncio
    async def test_handle_contact_no_pending(self):
        from src.bot.telegram_bot import _handle_contact_email

        update = _make_update("yahav@gmail.com")
        context = _make_context()
        # No pending_contact set

        await _handle_contact_email("yahav@gmail.com", update, context)

        update.message.reply_text.assert_called_with(
            "No pending contact resolution. Please start over."
        )
