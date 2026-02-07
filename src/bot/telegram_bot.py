"""
LifeOS Assistant — Telegram Bot.

Thin rendering adapter for the Telegram UI. All business logic lives in
ActionService; this module only handles Telegram-specific concerns:
authorization, keyboards, ConversationHandler state, and rendering
ServiceResponse objects as Telegram messages.

Security-first: unauthorized users are silently ignored.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import time as dt_time
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import settings
from src.core.action_service import (
    ActionService,
    BatchCancelPromptResponse,
    BatchSummaryResponse,
    ConflictPromptResponse,
    ContactPromptResponse,
    ErrorResponse,
    NoActionResponse,
    PendingBatchCancel,
    PendingEvent,
    QueryResultResponse,
    ServiceResponse,
    SuccessResponse,
)

if TYPE_CHECKING:
    from src.ports.calendar_port import CalendarPort
    from src.ports.notification_port import NotificationPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security: silent-ignore decorator
# ---------------------------------------------------------------------------


def authorized_only(
    func: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Decorator that silently ignores messages from unauthorized users.

    Does NOT send any response to strangers — the bot must not reveal
    its existence to unauthorized users.
    """

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.id not in settings.ALLOWED_USER_IDS:
            uid = user.id if user else "unknown"
            logger.warning("Unauthorized access attempt from user_id=%s", uid)
            return  # Silent ignore
        return await func(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# Response rendering — maps ServiceResponse → Telegram messages/keyboards
# ---------------------------------------------------------------------------


async def _render_response(
    response: ServiceResponse,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Map a ServiceResponse to Telegram messages and keyboards."""
    if isinstance(response, ConflictPromptResponse):
        context.user_data["pending_event"] = response.pending
        buttons = [
            [InlineKeyboardButton(opt.label, callback_data=f"conflict:{opt.key}")]
            for opt in response.options
        ]
        await update.message.reply_text(
            response.message, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif isinstance(response, BatchCancelPromptResponse):
        context.user_data["pending_batch_cancel"] = response.pending
        buttons = [
            [InlineKeyboardButton("Confirm cancel", callback_data="batchcancel:confirm")],
            [InlineKeyboardButton("Abort", callback_data="batchcancel:abort")],
        ]
        await update.message.reply_text(
            response.message, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif isinstance(response, ContactPromptResponse):
        context.user_data["pending_contact"] = response.pending
        context.user_data["awaiting_contact_email"] = True
        await update.message.reply_text(response.message, parse_mode="Markdown")

    elif isinstance(response, SuccessResponse):
        msg = response.message
        if response.event and response.event.link:
            msg += f"\n[Open in Google Calendar]({response.event.link})"
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif isinstance(response, (QueryResultResponse, BatchSummaryResponse)):
        await update.message.reply_text(response.message, parse_mode="Markdown")

    elif isinstance(response, (NoActionResponse, ErrorResponse)):
        await update.message.reply_text(response.message)


# ---------------------------------------------------------------------------
# Text processing — delegates to ActionService
# ---------------------------------------------------------------------------


async def _process_text(
    text: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Parse text via ActionService, render the result."""
    if context.user_data.get("awaiting_contact_email"):
        await _handle_contact_email(text, update, context)
        return

    if context.user_data.get("awaiting_custom_time"):
        await _handle_custom_time(text, update, context)
        return

    service: ActionService = context.bot_data["action_service"]
    response = await service.process_text(text)
    await _render_response(response, update, context)


# ---------------------------------------------------------------------------
# Conflict resolution callbacks
# ---------------------------------------------------------------------------


async def _handle_conflict_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle inline keyboard taps for conflict resolution."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if user is None or user.id not in settings.ALLOWED_USER_IDS:
        return

    pending = context.user_data.get("pending_event")
    if not pending:
        await query.edit_message_text("No pending event found. Please try again.")
        return

    action = query.data.split(":")[1]

    if action == "cancel":
        context.user_data.pop("pending_event", None)
        await query.edit_message_text("Event creation cancelled.")
        return

    if action == "custom":
        context.user_data["awaiting_custom_time"] = True
        await query.edit_message_text(
            "Please type the time you want (HH:MM format, e.g. 15:30):"
        )
        return

    service: ActionService = context.bot_data["action_service"]
    response = await service.resolve_conflict(pending, action)

    msg = response.message
    if isinstance(response, SuccessResponse) and response.event and response.event.link:
        msg += f"\n[Open in Google Calendar]({response.event.link})"
    await query.edit_message_text(msg, parse_mode="Markdown")

    context.user_data.pop("pending_event", None)


async def _handle_custom_time(
    text: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle user-typed custom time during conflict resolution."""
    context.user_data.pop("awaiting_custom_time", None)

    pending = context.user_data.get("pending_event")
    if not pending:
        await update.message.reply_text("No pending event. Please start over.")
        return

    service: ActionService = context.bot_data["action_service"]
    response = await service.resolve_conflict(pending, "custom", custom_time=text)

    if isinstance(response, SuccessResponse):
        msg = response.message
        if response.event and response.event.link:
            msg += f"\n[Open in Google Calendar]({response.event.link})"
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif isinstance(response, ErrorResponse):
        await update.message.reply_text(response.message)

    context.user_data.pop("pending_event", None)


# ---------------------------------------------------------------------------
# Contact email resolution
# ---------------------------------------------------------------------------


async def _handle_contact_email(
    text: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle user-typed email for contact resolution."""
    context.user_data.pop("awaiting_contact_email", None)

    pending = context.user_data.get("pending_contact")
    if not pending:
        await update.message.reply_text("No pending contact resolution. Please start over.")
        return

    service: ActionService = context.bot_data["action_service"]
    response = await service.resolve_contact(pending, text.strip())

    if isinstance(response, ContactPromptResponse):
        # More contacts to resolve — stay in the flow
        context.user_data["pending_contact"] = response.pending
        context.user_data["awaiting_contact_email"] = True
        await update.message.reply_text(response.message, parse_mode="Markdown")
    else:
        # Done resolving — render the final result
        context.user_data.pop("pending_contact", None)
        await _render_response(response, update, context)


# ---------------------------------------------------------------------------
# Batch cancel confirmation callback
# ---------------------------------------------------------------------------


async def _handle_batch_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle confirmation/abort for cancel-all-except flow."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if user is None or user.id not in settings.ALLOWED_USER_IDS:
        return

    action = query.data.split(":")[1]
    pending = context.user_data.pop("pending_batch_cancel", None)

    if action == "abort":
        await query.edit_message_text("Batch cancel aborted.")
        return

    if action == "confirm":
        if not pending:
            await query.edit_message_text("No pending cancel found. Please try again.")
            return

        service: ActionService = context.bot_data["action_service"]
        response = await service.confirm_batch_cancel(pending)
        await query.edit_message_text(response.message, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


@authorized_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    await update.message.reply_text(
        "Welcome to *LifeOS Assistant*!\n\n"
        "I help you manage your calendar and chores:\n"
        "• Send me a text or voice message to create a calendar event\n"
        "• Use /today to see today's schedule\n"
        "• Use /addchore to add a recurring chore\n"
        "• Use /chores to list chores, /done to mark one complete\n\n"
        "Type /help for the full command list.",
        parse_mode="Markdown",
    )


@authorized_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — list available commands."""
    await update.message.reply_text(
        "*Available commands:*\n"
        "/today — View today's calendar events\n"
        "/addchore — Add a recurring chore\n"
        "/chores — List all active chores\n"
        "/done <id> — Mark a chore as done\n"
        "/deletechore <id> — Delete a chore and its calendar events\n"
        "/help — Show this message",
        parse_mode="Markdown",
    )


@authorized_only
async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /today — show today's calendar events."""
    service: ActionService = context.bot_data["action_service"]
    response = await service.get_today_events()
    await update.message.reply_text(response.message, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Chore commands
# ---------------------------------------------------------------------------

# ConversationHandler states for /addchore
(
    CHORE_NAME,
    CHORE_FREQ,
    CHORE_DURATION,
    CHORE_TIME_PREF,
    CHORE_WEEKS,
    CHORE_CONFIRM,
) = range(6)

# Mapping for natural-language time preferences
_TIME_PREF_MAP = {
    "mornings": ("06:00", "12:00"),
    "morning": ("06:00", "12:00"),
    "afternoons": ("12:00", "17:00"),
    "afternoon": ("12:00", "17:00"),
    "evenings": ("17:00", "21:00"),
    "evening": ("17:00", "21:00"),
}


def _parse_time_pref(text: str) -> tuple[str, str] | None:
    """Parse a time preference string into (start, end) times.

    Accepts: 'mornings', 'evenings', '17:00-20:00', etc.
    Returns None if the input can't be parsed.
    """
    text = text.strip().lower()
    if text in _TIME_PREF_MAP:
        return _TIME_PREF_MAP[text]
    # Try HH:MM-HH:MM format
    if "-" in text:
        parts = text.split("-", 1)
        try:
            from datetime import datetime as _dt
            _dt.strptime(parts[0].strip(), "%H:%M")
            _dt.strptime(parts[1].strip(), "%H:%M")
            return (parts[0].strip(), parts[1].strip())
        except ValueError:
            pass
    return None


@authorized_only
async def cmd_addchore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /addchore — start chore creation conversation."""
    await update.message.reply_text("What's the chore name? (e.g., 'Take out trash')")
    return CHORE_NAME


async def addchore_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive chore name, ask for frequency."""
    context.user_data["chore_name"] = update.message.text.strip()
    keyboard = ReplyKeyboardMarkup(
        [["1", "2", "3"], ["4", "5", "7"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "How many times a week?",
        reply_markup=keyboard,
    )
    return CHORE_FREQ


async def addchore_freq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive times-per-week, convert to frequency_days, ask for duration."""
    text = update.message.text.strip()
    try:
        times_per_week = int(text)
        if times_per_week < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a number (e.g., 2 for twice a week).")
        return CHORE_FREQ
    # Convert times-per-week → every N days (e.g., 2/week → every 3 days)
    freq_days = max(1, 7 // times_per_week)
    context.user_data["chore_freq"] = freq_days
    context.user_data["chore_times_per_week"] = times_per_week
    keyboard = ReplyKeyboardMarkup(
        [["15", "30", "45"], ["60", "90", "120"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "How long does it take (in minutes)?",
        reply_markup=keyboard,
    )
    return CHORE_DURATION


async def addchore_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive duration, ask for time preference."""
    text = update.message.text.strip()
    try:
        duration = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a number of minutes (e.g., 30).")
        return CHORE_DURATION
    context.user_data["chore_duration"] = duration
    # Auto-assign to the Telegram user
    user = update.effective_user
    context.user_data["chore_assigned"] = user.first_name if user else "Me"
    keyboard = ReplyKeyboardMarkup(
        [["Mornings", "Afternoons", "Evenings"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "When do you prefer to do it?\n"
        "Pick an option or type a custom range (e.g., '17:00-20:00').",
        reply_markup=keyboard,
    )
    return CHORE_TIME_PREF


async def addchore_time_pref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive time preference, ask for weeks ahead."""
    text = update.message.text.strip()
    parsed = _parse_time_pref(text)
    if parsed is None:
        await update.message.reply_text(
            "I couldn't understand that. Please try: 'mornings', 'evenings', "
            "or a range like '17:00-20:00'."
        )
        return CHORE_TIME_PREF
    context.user_data["chore_time_start"], context.user_data["chore_time_end"] = parsed
    keyboard = ReplyKeyboardMarkup(
        [["2", "4", "6", "8"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "How many weeks ahead should I schedule?\n"
        "Pick a quick option or type any number.",
        reply_markup=keyboard,
    )
    return CHORE_WEEKS


async def addchore_weeks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive weeks ahead, find best recurring slot and present proposal."""
    text = update.message.text.strip()
    try:
        weeks = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a number (e.g., 4).")
        return CHORE_WEEKS

    context.user_data["chore_weeks"] = weeks

    await update.message.reply_text("Finding the best time slot...")

    service: ActionService = context.bot_data["action_service"]

    try:
        slot = await service.find_chore_slot(
            name=context.user_data["chore_name"],
            frequency_days=context.user_data["chore_freq"],
            duration_minutes=context.user_data["chore_duration"],
            preferred_start=context.user_data["chore_time_start"],
            preferred_end=context.user_data["chore_time_end"],
            weeks_ahead=weeks,
        )
    except Exception as exc:
        logger.error("Slot finding error: %s", exc)
        await update.message.reply_text(
            "Sorry, couldn't find a slot. Please try again."
        )
        return ConversationHandler.END

    if slot is None:
        await update.message.reply_text(
            "Couldn't find any open slot in the requested time range. "
            "Try a wider time window or fewer weeks."
        )
        return ConversationHandler.END

    context.user_data["chore_slot"] = slot

    freq = context.user_data["chore_freq"]
    lines = [
        f"*Proposed recurring schedule for '{context.user_data['chore_name']}':*\n",
        f"  Starting: {slot['start_date']}",
        f"  Time: {slot['start_time']}\u2013{slot['end_time']}",
        f"  Repeats: every {freq} day(s)",
        f"  Occurrences: {slot['occurrences']}",
        "\n_This will create a single recurring calendar event._",
        "_You can delete the entire series from Google Calendar._",
        "\nConfirm?",
    ]

    keyboard = ReplyKeyboardMarkup(
        [["Yes", "No"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard,
    )
    return CHORE_CONFIRM


async def addchore_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user confirmation — create DB entry and recurring calendar event."""
    from src.ports.calendar_port import CalendarError

    service: ActionService = context.bot_data["action_service"]

    answer = update.message.text.strip().lower()
    if answer not in ("yes", "y"):
        await update.message.reply_text(
            "Chore scheduling cancelled.", reply_markup=ReplyKeyboardRemove(),
        )
        _clear_chore_data(context)
        return ConversationHandler.END

    name = context.user_data["chore_name"]
    freq = context.user_data["chore_freq"]
    duration = context.user_data["chore_duration"]
    assigned = context.user_data["chore_assigned"]
    time_start = context.user_data["chore_time_start"]
    time_end = context.user_data["chore_time_end"]
    slot = context.user_data["chore_slot"]

    # Save chore to DB via service
    try:
        chore = service.create_chore(
            name=name,
            frequency_days=freq,
            assigned_to=assigned,
            duration_minutes=duration,
            preferred_time_start=time_start,
            preferred_time_end=time_end,
        )
    except Exception as exc:
        logger.error("Failed to add chore: %s", exc)
        await update.message.reply_text("Sorry, couldn't save the chore. Please try again.")
        _clear_chore_data(context)
        return ConversationHandler.END

    # Create recurring calendar event via service
    cal_response = await service.create_chore_calendar_event(chore, slot)

    if isinstance(cal_response, SuccessResponse):
        times_pw = context.user_data.get("chore_times_per_week", "?")
        link = cal_response.event.link if cal_response.event else ""
        msg = (
            f"\u2705 Chore *{name}* scheduled!\n"
            f"\u2022 {times_pw}x per week, {slot['occurrences']} occurrences\n"
            f"\u2022 Time: {slot['start_time']}\u2013{slot['end_time']}\n"
            f"\u2022 Starting: {slot['start_date']}"
        )
        if link:
            msg += f"\n[Open in Google Calendar]({link})"
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            f"\u2705 Chore *{name}* saved to DB, but the calendar event "
            f"couldn't be created. Error: {cal_response.message}",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )

    _clear_chore_data(context)
    return ConversationHandler.END


async def addchore_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel chore creation."""
    _clear_chore_data(context)
    await update.message.reply_text(
        "Chore creation cancelled.", reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def _clear_chore_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all chore-related keys from user_data."""
    keys = [
        "chore_name", "chore_freq", "chore_times_per_week", "chore_duration",
        "chore_assigned", "chore_time_start", "chore_time_end",
        "chore_weeks", "chore_slot",
    ]
    for k in keys:
        context.user_data.pop(k, None)


@authorized_only
async def cmd_chores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /chores — list all active chores."""
    service: ActionService = context.bot_data["action_service"]

    try:
        chores = service.list_chores(active_only=True)
    except Exception as exc:
        logger.error("/chores error: %s", exc)
        await update.message.reply_text("Couldn't load chores. Please try again.")
        return

    if not chores:
        await update.message.reply_text("No active chores.")
        return

    lines = ["*Active chores:*\n"]
    for c in chores:
        lines.append(f"`{c.id}` \u2014 {c.name} (due: {c.next_due}, assigned: {c.assigned_to})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized_only
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done <id> — mark a chore as done."""
    service: ActionService = context.bot_data["action_service"]

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /done <chore_id>\nUse /chores to see IDs.")
        return

    try:
        chore_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid chore ID. Use /chores to see valid IDs.")
        return

    try:
        chore = service.mark_chore_done(chore_id)
        await update.message.reply_text(
            f"\u2705 Marked '*{chore.name}*' as done. Next due: {chore.next_due}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("/done error: %s", exc)
        await update.message.reply_text(f"Couldn't mark chore {chore_id} as done. Please check the ID.")


@authorized_only
async def cmd_deletechore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deletechore — show active chores as buttons to pick from."""
    service: ActionService = context.bot_data["action_service"]

    try:
        chores = service.list_chores(active_only=True)
    except Exception as exc:
        logger.error("/deletechore error: %s", exc)
        await update.message.reply_text("Couldn't load chores. Please try again.")
        return

    if not chores:
        await update.message.reply_text("No active chores to delete.")
        return

    keyboard = [
        [InlineKeyboardButton(c.name, callback_data=f"delchore:{c.id}")]
        for c in chores
    ]
    await update.message.reply_text(
        "Which chore do you want to delete?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_deletechore_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle the inline button tap to delete a chore."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if user is None or user.id not in settings.ALLOWED_USER_IDS:
        return

    chore_id = int(query.data.split(":")[1])

    service: ActionService = context.bot_data["action_service"]
    response = await service.delete_chore(chore_id)
    await query.edit_message_text(response.message, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------


@authorized_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages — parse and create calendar event."""
    processing_msg = await update.message.reply_text("Processing...")
    await _process_text(update.message.text, update, context)
    try:
        await processing_msg.delete()
    except Exception:
        pass  # Non-critical if delete fails


@authorized_only
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages — transcribe via Whisper, then parse → calendar."""
    from src.core.transcriber import transcribe_audio

    voice = update.message.voice
    tmp_path: str | None = None

    try:
        # Download voice file to a temp directory
        voice_file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await voice_file.download_to_drive(tmp_path)

        # Transcribe
        text = await transcribe_audio(tmp_path)
        logger.info("Voice transcribed: %s", text[:80])

        # Show what was heard, then process
        await update.message.reply_text(f"\U0001f3a4 I heard: {text}")
        await _process_text(text, update, context)

    except Exception as exc:
        logger.error("Voice handling error: %s", exc)
        await update.message.reply_text(
            "Sorry, I couldn't process your voice message. Please try again."
        )
    finally:
        # Cleanup temp file
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


def build_app(
    calendar: CalendarPort | None = None,
    notifier: NotificationPort | None = None,
) -> Application:
    """Build and configure the Telegram Application with all handlers.

    Args:
        calendar: Calendar port implementation. Defaults to GoogleCalendarAdapter.
        notifier: Notification port implementation. Defaults to TelegramNotifier
                  (created from the bot instance after app is built).
    """
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Wire default adapters if not provided
    if calendar is None:
        from src.adapters.calendar_factory import create_calendar_adapter
        calendar = create_calendar_adapter()

    if notifier is None:
        from src.adapters.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier(app.bot)

    # Create the service layer with contact DB
    from src.data.db import ContactDB
    contact_db = ContactDB()
    service = ActionService(calendar, contact_db=contact_db)

    # Store ports and service in bot_data for handler access
    app.bot_data["calendar"] = calendar
    app.bot_data["notifier"] = notifier
    app.bot_data["action_service"] = service

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("chores", cmd_chores))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("deletechore", cmd_deletechore))
    app.add_handler(CallbackQueryHandler(_handle_deletechore_callback, pattern=r"^delchore:\d+$"))
    app.add_handler(CallbackQueryHandler(_handle_conflict_callback, pattern=r"^conflict:"))
    app.add_handler(CallbackQueryHandler(_handle_batch_cancel_callback, pattern=r"^batchcancel:"))

    # /addchore conversation handler
    _text = filters.TEXT & ~filters.COMMAND
    addchore_conv = ConversationHandler(
        entry_points=[CommandHandler("addchore", cmd_addchore)],
        states={
            CHORE_NAME: [MessageHandler(_text, addchore_name)],
            CHORE_FREQ: [MessageHandler(_text, addchore_freq)],
            CHORE_DURATION: [MessageHandler(_text, addchore_duration)],
            CHORE_TIME_PREF: [MessageHandler(_text, addchore_time_pref)],
            CHORE_WEEKS: [MessageHandler(_text, addchore_weeks)],
            CHORE_CONFIRM: [MessageHandler(_text, addchore_confirm)],
        },
        fallbacks=[CommandHandler("cancel", addchore_cancel)],
    )
    app.add_handler(addchore_conv)

    # Text messages (non-command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Voice messages
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Morning Briefing scheduler — Telegram-specific scheduling logic
    _setup_morning_briefing(app, calendar, notifier)

    logger.info("Telegram bot application built with %d handlers", len(app.handlers[0]))
    return app


def _setup_morning_briefing(
    app: Application,
    calendar: CalendarPort,
    notifier: NotificationPort,
) -> None:
    """Register the daily morning briefing job at 08:00 Asia/Jerusalem."""
    from src.core.scheduler import send_morning_summary

    tz = ZoneInfo(settings.TIMEZONE)
    briefing_time = dt_time(hour=settings.MORNING_BRIEFING_HOUR, minute=0, tzinfo=tz)

    async def _morning_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
        await send_morning_summary(calendar, notifier)

    app.job_queue.run_daily(
        _morning_job_callback,
        time=briefing_time,
        name="morning_briefing",
    )

    logger.info(
        "Morning briefing scheduled at %02d:00 %s",
        settings.MORNING_BRIEFING_HOUR,
        settings.TIMEZONE,
    )


def main() -> None:
    """Entry point: build the app and start polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting LifeOS Assistant bot...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
