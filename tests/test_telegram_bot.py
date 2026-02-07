"""Tests for src.bot.telegram_bot — Telegram bot handlers.

Tests the conversation flow logic, command handlers, and authorization.
All external dependencies (DB, GCal, LLM) are mocked.
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
# Tests for addchore conversation handlers
# ---------------------------------------------------------------------------


def _make_update(text, user_id=12345, first_name="Amit"):
    """Create a mock Update with a text message from an authorized user."""
    update = MagicMock()
    update.message.text = text
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.message.reply_text = AsyncMock()
    return update


def _make_context(calendar=None):
    """Create a mock context with user_data dict and bot_data with calendar port."""
    context = MagicMock()
    context.user_data = {}
    mock_cal = calendar or MagicMock()
    context.bot_data = {"calendar": mock_cal}
    return context


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

        update = _make_update("4")
        context = _make_context()
        context.user_data.update({
            "chore_name": "Test",
            "chore_freq": 7,
            "chore_duration": 30,
            "chore_time_start": "17:00",
            "chore_time_end": "21:00",
        })

        mock_slot = {
            "start_date": "2026-02-08",
            "start_time": "17:00",
            "end_time": "17:30",
            "occurrences": 4,
            "frequency_days": 7,
        }
        with patch("src.core.chore_scheduler.find_best_slot", AsyncMock(return_value=mock_slot)):
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

        mock_created = {"id": "gcal123", "htmlLink": "https://..."}
        mock_cal = MagicMock()
        mock_cal.add_recurring_event = AsyncMock(return_value=mock_created)

        update = _make_update("yes")
        context = _make_context(calendar=mock_cal)
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

        mock_chore = MagicMock()
        mock_chore.id = 1
        mock_db = MagicMock()
        mock_db.add_chore.return_value = mock_chore

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            result = await addchore_confirm(update, context)

        assert result == ConversationHandler.END
        mock_db.add_chore.assert_called_once()
        mock_db.set_calendar_event_id.assert_called_once_with(1, "gcal123")
        mock_cal.add_recurring_event.assert_called_once()

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

        update = _make_update("/chores")
        context = _make_context()

        mock_db = MagicMock()
        mock_db.list_all.return_value = []

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            await cmd_chores(update, context)
        update.message.reply_text.assert_called_with("No active chores.")

    @pytest.mark.asyncio
    async def test_lists_active_chores(self):
        from src.bot.telegram_bot import cmd_chores
        from src.data.models import Chore

        update = _make_update("/chores")
        context = _make_context()

        mock_chore = Chore(
            id=1, name="Trash", frequency_days=7, duration_minutes=15,
            preferred_time_start="09:00", preferred_time_end="21:00",
            next_due="2026-02-07", assigned_to="Amit",
        )
        mock_db = MagicMock()
        mock_db.list_all.return_value = [mock_chore]

        with patch("src.data.db.ChoreDB", return_value=mock_db):
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

        update = _make_update("/deletechore")
        context = _make_context()

        chores = [
            Chore(id=1, name="Trash", frequency_days=7, duration_minutes=15,
                  preferred_time_start="09:00", preferred_time_end="21:00",
                  next_due="2026-02-07", assigned_to="Amit"),
            Chore(id=2, name="Vacuum", frequency_days=3, duration_minutes=30,
                  preferred_time_start="17:00", preferred_time_end="21:00",
                  next_due="2026-02-07", assigned_to="Amit"),
        ]
        mock_db = MagicMock()
        mock_db.list_all.return_value = chores

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            await cmd_deletechore(update, context)

        call_kwargs = update.message.reply_text.call_args
        assert "Which chore" in call_kwargs[0][0]
        # Verify inline keyboard was passed
        assert call_kwargs[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_no_chores_to_delete(self):
        from src.bot.telegram_bot import cmd_deletechore

        update = _make_update("/deletechore")
        context = _make_context()

        mock_db = MagicMock()
        mock_db.list_all.return_value = []

        with patch("src.data.db.ChoreDB", return_value=mock_db):
            await cmd_deletechore(update, context)
        update.message.reply_text.assert_called_with("No active chores to delete.")


# ---------------------------------------------------------------------------
# Tests for conflict resolution flow
# ---------------------------------------------------------------------------


class TestConflictResolutionCreate:
    """Test conflict detection when creating events via _process_text."""

    @pytest.mark.asyncio
    async def test_create_no_conflict_proceeds(self):
        """When no conflict, event is created normally."""
        from src.bot.telegram_bot import _process_text
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock(return_value={"htmlLink": "https://cal/1"})

        update = _make_update("Meeting tomorrow at 14:00")
        context = _make_context(calendar=mock_cal)

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=parsed)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            await _process_text("Meeting tomorrow at 14:00", update, context)

        mock_cal.add_event.assert_called_once()
        assert "pending_event" not in context.user_data

    @pytest.mark.asyncio
    async def test_create_with_conflict_shows_keyboard(self):
        """When conflict detected, shows inline keyboard instead of creating."""
        from src.bot.telegram_bot import _process_text
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock()

        update = _make_update("Meeting at 14:00")
        context = _make_context(calendar=mock_cal)

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Existing meeting", "start_time": "14:00", "end_time": "15:00"}],
            suggested_time="15:00",
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=parsed)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=conflict)):
            await _process_text("Meeting at 14:00", update, context)

        # Event should NOT have been created
        mock_cal.add_event.assert_not_called()
        # Pending event should be stored
        assert "pending_event" in context.user_data
        assert context.user_data["pending_event"]["type"] == "create"
        # Reply should contain conflict info and inline keyboard
        reply_call = update.message.reply_text.call_args
        assert "conflict" in reply_call[0][0].lower()
        assert reply_call[1]["reply_markup"] is not None


class TestConflictResolutionReschedule:
    """Test conflict detection when rescheduling events."""

    @pytest.mark.asyncio
    async def test_reschedule_with_conflict_shows_keyboard(self):
        from src.bot.telegram_bot import _process_text
        from src.core.parser import RescheduleEvent
        from src.core.conflict_checker import ConflictResult

        matched_event = {
            "id": "ev1", "summary": "My Meeting",
            "start_time": "10:00", "end_time": "11:00",
        }
        mock_cal = MagicMock()
        mock_cal.find_events = AsyncMock(return_value=[matched_event])
        mock_cal.update_event = AsyncMock()

        update = _make_update("Reschedule meeting to 14:00")
        context = _make_context(calendar=mock_cal)

        parsed = RescheduleEvent(
            event_summary="My Meeting",
            original_date="2026-02-08",
            new_time="14:00",
        )
        conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Blocker", "start_time": "14:00", "end_time": "15:00"}],
            suggested_time="15:00",
        )

        with patch("src.core.parser.parse_message", AsyncMock(return_value=parsed)), \
             patch("src.core.parser.match_event", AsyncMock(return_value=matched_event)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=conflict)):
            await _process_text("Reschedule meeting to 14:00", update, context)

        mock_cal.update_event.assert_not_called()
        assert context.user_data["pending_event"]["type"] == "reschedule"
        assert context.user_data["pending_event"]["event_id"] == "ev1"

    @pytest.mark.asyncio
    async def test_reschedule_self_exclusion_no_conflict(self):
        """When rescheduling to same time, self-exclusion means no conflict."""
        from src.bot.telegram_bot import _process_text
        from src.core.parser import RescheduleEvent
        from src.core.conflict_checker import ConflictResult

        matched_event = {
            "id": "ev1", "summary": "My Meeting",
            "start_time": "10:00", "end_time": "11:00",
        }
        mock_cal = MagicMock()
        mock_cal.find_events = AsyncMock(return_value=[matched_event])
        mock_cal.update_event = AsyncMock(return_value={
            "summary": "My Meeting", "htmlLink": "https://cal/1",
        })

        update = _make_update("Reschedule meeting to 15:00")
        context = _make_context(calendar=mock_cal)

        parsed = RescheduleEvent(
            event_summary="My Meeting",
            original_date="2026-02-08",
            new_time="15:00",
        )
        no_conflict = ConflictResult(has_conflict=False)

        with patch("src.core.parser.parse_message", AsyncMock(return_value=parsed)), \
             patch("src.core.parser.match_event", AsyncMock(return_value=matched_event)), \
             patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            await _process_text("Reschedule meeting to 15:00", update, context)

        mock_cal.update_event.assert_called_once()


class TestConflictCallbackHandler:
    """Test the inline keyboard callback for conflict resolution."""

    def _make_callback_update(self, callback_data, user_id=12345, button_text="Use 15:00"):
        """Create a mock Update with a callback query."""
        update = MagicMock()
        update.callback_query.data = callback_data
        update.callback_query.from_user.id = user_id
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        # Mock the reply_markup to contain the suggested time button
        btn = MagicMock()
        btn.callback_data = "conflict:suggested"
        btn.text = button_text
        update.callback_query.message.reply_markup.inline_keyboard = [[btn]]
        return update

    @pytest.mark.asyncio
    async def test_callback_suggested(self):
        from src.bot.telegram_bot import _handle_conflict_callback
        from src.core.parser import ParsedEvent

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        update = self._make_callback_update("conflict:suggested")
        context = _make_context(calendar=mock_cal)
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        await _handle_conflict_callback(update, context)

        mock_cal.add_event.assert_called_once()
        assert parsed.time == "15:00"  # Updated to suggested time
        assert "pending_event" not in context.user_data

    @pytest.mark.asyncio
    async def test_callback_force(self):
        from src.bot.telegram_bot import _handle_conflict_callback
        from src.core.parser import ParsedEvent

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        update = self._make_callback_update("conflict:force")
        context = _make_context(calendar=mock_cal)
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        await _handle_conflict_callback(update, context)

        mock_cal.add_event.assert_called_once()
        assert parsed.time == "14:00"  # Kept original time
        assert "pending_event" not in context.user_data

    @pytest.mark.asyncio
    async def test_callback_custom_prompts_user(self):
        from src.bot.telegram_bot import _handle_conflict_callback
        from src.core.parser import ParsedEvent

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")

        update = self._make_callback_update("conflict:custom")
        context = _make_context()
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        await _handle_conflict_callback(update, context)

        assert context.user_data.get("awaiting_custom_time") is True
        update.callback_query.edit_message_text.assert_called_once()
        assert "HH:MM" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_callback_cancel(self):
        from src.bot.telegram_bot import _handle_conflict_callback
        from src.core.parser import ParsedEvent

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")

        update = self._make_callback_update("conflict:cancel")
        context = _make_context()
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

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

        mock_cal = MagicMock()
        mock_cal.update_event = AsyncMock(return_value={
            "summary": "My Meeting", "htmlLink": "",
        })

        update = self._make_callback_update("conflict:force")
        context = _make_context(calendar=mock_cal)
        context.user_data["pending_event"] = {
            "type": "reschedule",
            "event_id": "ev1",
            "date": "2026-02-08",
            "time": "14:00",
            "duration": 60,
            "summary": "My Meeting",
        }

        await _handle_conflict_callback(update, context)

        mock_cal.update_event.assert_called_once_with("ev1", "2026-02-08", "14:00")
        assert "pending_event" not in context.user_data


class TestCustomTimeHandler:
    """Test the custom time input during conflict resolution."""

    @pytest.mark.asyncio
    async def test_valid_custom_time_creates_event(self):
        from src.bot.telegram_bot import _process_text
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        update = _make_update("16:30")
        context = _make_context(calendar=mock_cal)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        no_conflict = ConflictResult(has_conflict=False)
        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=no_conflict)):
            await _process_text("16:30", update, context)

        mock_cal.add_event.assert_called_once()
        assert parsed.time == "16:30"
        assert "pending_event" not in context.user_data
        assert "awaiting_custom_time" not in context.user_data

    @pytest.mark.asyncio
    async def test_invalid_format_cancels(self):
        from src.bot.telegram_bot import _process_text
        from src.core.parser import ParsedEvent

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")

        update = _make_update("not-a-time")
        context = _make_context()
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        await _process_text("not-a-time", update, context)

        assert "pending_event" not in context.user_data
        assert "Invalid time" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_custom_time_with_still_conflicting_warns_and_proceeds(self):
        from src.bot.telegram_bot import _process_text
        from src.core.parser import ParsedEvent
        from src.core.conflict_checker import ConflictResult

        parsed = ParsedEvent(event="Meeting", date="2026-02-08", time="14:00")
        mock_cal = MagicMock()
        mock_cal.add_event = AsyncMock(return_value={"htmlLink": ""})

        update = _make_update("15:00")
        context = _make_context(calendar=mock_cal)
        context.user_data["awaiting_custom_time"] = True
        context.user_data["pending_event"] = {"type": "create", "parsed": parsed}

        still_conflict = ConflictResult(
            has_conflict=True,
            conflicting_events=[{"summary": "Another meeting"}],
        )
        with patch("src.core.conflict_checker.check_conflict", AsyncMock(return_value=still_conflict)):
            await _process_text("15:00", update, context)

        # Should still create the event (warns but proceeds)
        mock_cal.add_event.assert_called_once()
        # First call is the warning, second is the success message
        calls = update.message.reply_text.call_args_list
        assert any("also conflicts" in str(c) for c in calls)
