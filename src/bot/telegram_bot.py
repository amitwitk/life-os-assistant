"""
LifeOS Assistant — Telegram Bot.

Telegram is the only user interface — the single gateway to LifeOS.
Every interaction (text capture, voice capture, chore management, daily
briefings) flows through this bot.

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

from src.ports.calendar_port import CalendarError
from src.config import settings

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
# Capture System: parse text → create calendar event
# ---------------------------------------------------------------------------


async def _process_text(
    text: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Shared logic: parse text via LLM -> create or cancel calendar event."""
    from src.core.conflict_checker import check_conflict, extract_event_duration_minutes
    from src.core.parser import CancelEvent, ParsedEvent, QueryEvents, RescheduleEvent, match_event, parse_message

    # Intercept custom time input if awaiting one
    if context.user_data.get("awaiting_custom_time"):
        await _handle_custom_time(text, update, context)
        return

    calendar: CalendarPort = context.bot_data["calendar"]

    try:
        parsed = await parse_message(text)
    except Exception as exc:
        logger.error("Parser error: %s", exc)
        await update.message.reply_text(
            "Sorry, something went wrong while parsing your message. Please try again."
        )
        return

    if parsed is None:
        await update.message.reply_text(
            "I couldn't find any actionable information in your message. "
            "Try something like: 'Meeting with Dan tomorrow at 14:00', "
            "'Cancel my meeting with Dan tomorrow', or "
            "'Reschedule my meeting with Dan tomorrow to 15:00'."
        )
        return

    if isinstance(parsed, ParsedEvent):
        # Check for conflicts before creating
        conflict = await check_conflict(
            calendar, parsed.date, parsed.time, parsed.duration_minutes,
        )
        if conflict.has_conflict:
            context.user_data["pending_event"] = {
                "type": "create",
                "parsed": parsed,
            }
            await _send_conflict_message(update, conflict, parsed.time)
            return

        try:
            created = await calendar.add_event(parsed)
            link = created.get("htmlLink", "")
            msg = f"✅ Event created: *{parsed.event}* on {parsed.date} at {parsed.time}"
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except CalendarError as exc:
            logger.error("Calendar write error: %s", exc)
            await update.message.reply_text(
                "I parsed your event but couldn't save it to Google Calendar. "
                "Please try again later."
            )
    elif isinstance(parsed, CancelEvent):
        try:
            all_events = await calendar.find_events(target_date=parsed.date)
            if not all_events:
                await update.message.reply_text(
                    f"There are no events on {parsed.date} to cancel."
                )
                return

            matched = await match_event(parsed.event_summary, all_events)
            if matched is None:
                summaries = ", ".join(ev["summary"] for ev in all_events)
                await update.message.reply_text(
                    f"I couldn't match '{parsed.event_summary}' to any event on {parsed.date}.\n"
                    f"Events that day: {summaries}"
                )
                return

            await calendar.delete_event(matched["id"])
            await update.message.reply_text(
                f"✅ Event canceled: *{matched['summary']}*",
                parse_mode="Markdown",
            )
        except CalendarError as exc:
            logger.error("Calendar delete error: %s", exc)
            await update.message.reply_text(
                "I found the event but couldn't cancel it. Please try again later."
            )
    elif isinstance(parsed, RescheduleEvent):
        try:
            all_events = await calendar.find_events(target_date=parsed.original_date)
            if not all_events:
                await update.message.reply_text(
                    f"There are no events on {parsed.original_date} to reschedule."
                )
                return

            matched = await match_event(parsed.event_summary, all_events)
            if matched is None:
                summaries = ", ".join(ev["summary"] for ev in all_events)
                await update.message.reply_text(
                    f"I couldn't match '{parsed.event_summary}' to any event on {parsed.original_date}.\n"
                    f"Events that day: {summaries}"
                )
                return

            # Check for conflicts at the new time (exclude the event being rescheduled)
            duration = extract_event_duration_minutes(matched)
            conflict = await check_conflict(
                calendar, parsed.original_date, parsed.new_time,
                duration, exclude_event_id=matched["id"],
            )
            if conflict.has_conflict:
                context.user_data["pending_event"] = {
                    "type": "reschedule",
                    "event_id": matched["id"],
                    "date": parsed.original_date,
                    "time": parsed.new_time,
                    "duration": duration,
                    "summary": matched.get("summary", "Unknown Event"),
                }
                await _send_conflict_message(update, conflict, parsed.new_time)
                return

            updated = await calendar.update_event(
                matched["id"], parsed.original_date, parsed.new_time
            )
            link = updated.get("htmlLink", "")
            msg = (
                f"✅ Event *{updated.get('summary', 'Unknown Event')}* "
                f"rescheduled to {parsed.original_date} at {parsed.new_time}"
            )
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except CalendarError as exc:
            logger.error("Calendar reschedule error: %s", exc)
            await update.message.reply_text(
                "I found the event but couldn't reschedule it. Please try again later."
            )
    elif isinstance(parsed, QueryEvents):
        try:
            events = await calendar.find_events(target_date=parsed.date)
            if not events:
                await update.message.reply_text(f"No events scheduled for {parsed.date}.")
                return

            lines = [f"*Events on {parsed.date}:*\n"]
            for ev in events:
                start = ev.get("start_time", "")
                if "T" in start:
                    start = start.split("T")[1][:5]
                end = ev.get("end_time", "")
                if "T" in end:
                    end = end.split("T")[1][:5]
                summary = ev.get("summary", "(no title)")
                lines.append(f"• {start} – {end}  {summary}")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except CalendarError as exc:
            logger.error("Calendar query error: %s", exc)
            await update.message.reply_text(
                "Couldn't fetch events. Please try again later."
            )


# ---------------------------------------------------------------------------
# Conflict resolution helpers
# ---------------------------------------------------------------------------


async def _send_conflict_message(
    update: Update,
    conflict: "ConflictResult",
    original_time: str,
) -> None:
    """Send a conflict notification with resolution options."""
    from src.core.conflict_checker import ConflictResult

    clashing = ", ".join(
        ev.get("summary", "(no title)") for ev in conflict.conflicting_events
    )
    msg = (
        f"⚠️ *Time conflict detected!*\n"
        f"Your requested time ({original_time}) overlaps with: {clashing}"
    )

    buttons = []
    if conflict.suggested_time:
        buttons.append([InlineKeyboardButton(
            f"Use {conflict.suggested_time}", callback_data="conflict:suggested",
        )])
    buttons.append([InlineKeyboardButton(
        f"Force {original_time}", callback_data="conflict:force",
    )])
    buttons.append([InlineKeyboardButton(
        "Enter custom time", callback_data="conflict:custom",
    )])
    buttons.append([InlineKeyboardButton(
        "Cancel", callback_data="conflict:cancel",
    )])

    await update.message.reply_text(
        msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _handle_conflict_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline keyboard taps for conflict resolution."""
    from src.core.conflict_checker import ConflictResult

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

    calendar: CalendarPort = context.bot_data["calendar"]

    if action == "suggested":
        # Use the suggested time from the conflict result
        # Re-read the suggested time from the keyboard button text
        suggested_time = None
        if query.message and query.message.reply_markup:
            for row in query.message.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data == "conflict:suggested":
                        # Extract HH:MM from button text like "Use 15:30"
                        suggested_time = btn.text.replace("Use ", "")
                        break
        if not suggested_time:
            await query.edit_message_text("Could not determine suggested time. Please try again.")
            context.user_data.pop("pending_event", None)
            return
        time_to_use = suggested_time
    elif action == "force":
        if pending["type"] == "create":
            time_to_use = pending["parsed"].time
        else:
            time_to_use = pending["time"]
    else:
        context.user_data.pop("pending_event", None)
        return

    await _execute_pending_event(pending, time_to_use, calendar, query)
    context.user_data.pop("pending_event", None)


async def _execute_pending_event(
    pending: dict, time_to_use: str, calendar: "CalendarPort", query: Any,
) -> None:
    """Execute the pending event creation or reschedule at the given time."""
    try:
        if pending["type"] == "create":
            parsed = pending["parsed"]
            parsed.time = time_to_use
            created = await calendar.add_event(parsed)
            link = created.get("htmlLink", "")
            msg = f"✅ Event created: *{parsed.event}* on {parsed.date} at {time_to_use}"
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await query.edit_message_text(msg, parse_mode="Markdown")
        else:
            updated = await calendar.update_event(
                pending["event_id"], pending["date"], time_to_use,
            )
            link = updated.get("htmlLink", "")
            summary = updated.get("summary", pending.get("summary", "Unknown Event"))
            msg = f"✅ Event *{summary}* rescheduled to {pending['date']} at {time_to_use}"
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await query.edit_message_text(msg, parse_mode="Markdown")
    except CalendarError as exc:
        logger.error("Calendar error during conflict resolution: %s", exc)
        await query.edit_message_text(
            "Something went wrong while saving the event. Please try again."
        )


async def _handle_custom_time(
    text: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle user-typed custom time during conflict resolution."""
    import re

    context.user_data.pop("awaiting_custom_time", None)

    pending = context.user_data.get("pending_event")
    if not pending:
        await update.message.reply_text("No pending event. Please start over.")
        return

    text = text.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        context.user_data.pop("pending_event", None)
        await update.message.reply_text(
            "Invalid time format. Please use HH:MM (e.g. 15:30). Event cancelled."
        )
        return

    calendar: CalendarPort = context.bot_data["calendar"]

    # Re-check conflict at the custom time (warn but proceed)
    from src.core.conflict_checker import check_conflict

    if pending["type"] == "create":
        parsed = pending["parsed"]
        conflict = await check_conflict(
            calendar, parsed.date, text, parsed.duration_minutes,
        )
        if conflict.has_conflict:
            clashing = ", ".join(
                ev.get("summary", "(no title)") for ev in conflict.conflicting_events
            )
            await update.message.reply_text(
                f"⚠️ Note: {text} also conflicts with: {clashing}. Proceeding anyway."
            )
        parsed.time = text
        try:
            created = await calendar.add_event(parsed)
            link = created.get("htmlLink", "")
            msg = f"✅ Event created: *{parsed.event}* on {parsed.date} at {text}"
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except CalendarError as exc:
            logger.error("Calendar error during custom time: %s", exc)
            await update.message.reply_text(
                "Couldn't save the event. Please try again later."
            )
    else:
        exclude_id = pending.get("event_id")
        conflict = await check_conflict(
            calendar, pending["date"], text,
            pending["duration"], exclude_event_id=exclude_id,
        )
        if conflict.has_conflict:
            clashing = ", ".join(
                ev.get("summary", "(no title)") for ev in conflict.conflicting_events
            )
            await update.message.reply_text(
                f"⚠️ Note: {text} also conflicts with: {clashing}. Proceeding anyway."
            )
        try:
            updated = await calendar.update_event(
                pending["event_id"], pending["date"], text,
            )
            link = updated.get("htmlLink", "")
            summary = updated.get("summary", pending.get("summary", "Unknown Event"))
            msg = f"✅ Event *{summary}* rescheduled to {pending['date']} at {text}"
            if link:
                msg += f"\n[Open in Google Calendar]({link})"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except CalendarError as exc:
            logger.error("Calendar error during custom time reschedule: %s", exc)
            await update.message.reply_text(
                "Couldn't reschedule the event. Please try again later."
            )

    context.user_data.pop("pending_event", None)


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
    calendar: CalendarPort = context.bot_data["calendar"]

    try:
        events = await calendar.get_daily_events()
    except CalendarError as exc:
        logger.error("/today calendar error: %s", exc)
        await update.message.reply_text("Couldn't fetch today's events. Please try again later.")
        return

    if not events:
        await update.message.reply_text("No events scheduled for today.")
        return

    lines = ["*Today's schedule:*\n"]
    for ev in events:
        start = ev.get("start_time", "")
        # Extract HH:MM from ISO datetime
        if "T" in start:
            start = start.split("T")[1][:5]
        end = ev.get("end_time", "")
        if "T" in end:
            end = end.split("T")[1][:5]
        summary = ev.get("summary", "(no title)")
        lines.append(f"• {start} – {end}  {summary}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Chore commands (uses ChoreDB from Phase 4)
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
    from src.core.chore_scheduler import find_best_slot

    text = update.message.text.strip()
    try:
        weeks = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a number (e.g., 4).")
        return CHORE_WEEKS

    context.user_data["chore_weeks"] = weeks

    await update.message.reply_text("Finding the best time slot...")

    calendar: CalendarPort = context.bot_data["calendar"]

    try:
        slot = await find_best_slot(
            calendar=calendar,
            chore_name=context.user_data["chore_name"],
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
        f"  Time: {slot['start_time']}–{slot['end_time']}",
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
    from src.data.db import ChoreDB

    calendar: CalendarPort = context.bot_data["calendar"]

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

    # Save chore to DB
    try:
        db = ChoreDB()
        chore = db.add_chore(
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

    # Create recurring calendar event
    try:
        created = await calendar.add_recurring_event(
            summary=f"🧹 {name}",
            description=f"Chore: {name}\nChore ID: {chore.id}",
            start_date=slot["start_date"],
            start_time=slot["start_time"],
            end_time=slot["end_time"],
            frequency_days=slot["frequency_days"],
            occurrences=slot["occurrences"],
        )
        # Link the calendar event to the chore in DB
        db.set_calendar_event_id(chore.id, created["id"])

        times_pw = context.user_data.get("chore_times_per_week", "?")
        link = created.get("htmlLink", "")
        msg = (
            f"✅ Chore *{name}* scheduled!\n"
            f"• {times_pw}x per week, {slot['occurrences']} occurrences\n"
            f"• Time: {slot['start_time']}–{slot['end_time']}\n"
            f"• Starting: {slot['start_date']}"
        )
        if link:
            msg += f"\n[Open in Google Calendar]({link})"
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove(),
        )
    except CalendarError as exc:
        logger.error("Calendar error: %s", exc)
        await update.message.reply_text(
            f"✅ Chore *{name}* saved to DB, but the calendar event "
            f"couldn't be created. Error: {exc}",
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
    from src.data.db import ChoreDB

    try:
        db = ChoreDB()
        chores = db.list_all(active_only=True)
    except Exception as exc:
        logger.error("/chores error: %s", exc)
        await update.message.reply_text("Couldn't load chores. Please try again.")
        return

    if not chores:
        await update.message.reply_text("No active chores.")
        return

    lines = ["*Active chores:*\n"]
    for c in chores:
        lines.append(f"`{c.id}` — {c.name} (due: {c.next_due}, assigned: {c.assigned_to})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized_only
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done <id> — mark a chore as done."""
    from src.data.db import ChoreDB

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
        db = ChoreDB()
        chore = db.mark_done(chore_id)
        await update.message.reply_text(
            f"✅ Marked '*{chore.name}*' as done. Next due: {chore.next_due}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("/done error: %s", exc)
        await update.message.reply_text(f"Couldn't mark chore {chore_id} as done. Please check the ID.")


@authorized_only
async def cmd_deletechore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deletechore — show active chores as buttons to pick from."""
    from src.data.db import ChoreDB

    try:
        db = ChoreDB()
        chores = db.list_all(active_only=True)
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
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the inline button tap to delete a chore."""
    from src.data.db import ChoreDB

    calendar: CalendarPort = context.bot_data["calendar"]

    query = update.callback_query
    await query.answer()

    # Verify the user is authorized
    user = query.from_user
    if user is None or user.id not in settings.ALLOWED_USER_IDS:
        return

    chore_id = int(query.data.split(":")[1])

    try:
        db = ChoreDB()
        chore = db.get_chore(chore_id)
        if chore is None or not chore.active:
            await query.edit_message_text("Chore not found or already deleted.")
            return

        # Delete the recurring calendar event if linked
        cal_deleted = False
        if chore.calendar_event_id:
            try:
                await calendar.delete_event(chore.calendar_event_id)
                cal_deleted = True
            except CalendarError as exc:
                logger.error("Failed to delete calendar event for chore #%d: %s", chore_id, exc)

        # Soft-delete the chore in DB
        db.delete_chore(chore_id)

        msg = f"✅ Chore *{chore.name}* deleted."
        if cal_deleted:
            msg += "\nAll linked calendar events have been removed."
        elif chore.calendar_event_id:
            msg += "\n⚠️ Couldn't remove the calendar events — please delete them manually."
        await query.edit_message_text(msg, parse_mode="Markdown")

    except Exception as exc:
        logger.error("deletechore callback error: %s", exc)
        await query.edit_message_text("Something went wrong. Please try again.")


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
        await update.message.reply_text(f"🎤 I heard: {text}")
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

    # Store ports in bot_data for handler access
    app.bot_data["calendar"] = calendar
    app.bot_data["notifier"] = notifier

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("chores", cmd_chores))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("deletechore", cmd_deletechore))
    app.add_handler(CallbackQueryHandler(_handle_deletechore_callback, pattern=r"^delchore:\d+$"))
    app.add_handler(CallbackQueryHandler(_handle_conflict_callback, pattern=r"^conflict:"))

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
