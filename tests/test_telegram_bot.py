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
