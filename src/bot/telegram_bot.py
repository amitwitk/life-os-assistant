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
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Coroutine

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import settings

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
    """Shared logic: parse text via LLM -> create or cancel Google Calendar event."""
    from src.core.parser import CancelEvent, ParsedEvent, parse_message
    from src.integrations.gcal_service import (
        CalendarError,
        add_event,
        delete_event,
        find_events,
    )

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
            "Try something like: 'Meeting with Dan tomorrow at 14:00' "
            "or 'Cancel my meeting with Dan tomorrow'."
        )
        return

    if isinstance(parsed, ParsedEvent):
        try:
            created = await add_event(parsed)
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
            events = await find_events(
                query=parsed.event_summary, target_date=parsed.date
            )
            if not events:
                await update.message.reply_text(
                    f"I couldn't find any event matching '{parsed.event_summary}' on {parsed.date}."
                )
                return

            # For now, just cancel the first match.
            # A more advanced version could ask the user for clarification if multiple events match.
            event_to_cancel = events[0]
            await delete_event(event_to_cancel["id"])
            await update.message.reply_text(
                f"✅ Event canceled: *{event_to_cancel['summary']}*",
                parse_mode="Markdown",
            )
        except CalendarError as exc:
            logger.error("Calendar delete error: %s", exc)
            await update.message.reply_text(
                "I found the event but couldn't cancel it. Please try again later."
            )


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
        "/help — Show this message",
        parse_mode="Markdown",
    )


@authorized_only
async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /today — show today's calendar events."""
    from src.integrations.gcal_service import CalendarError, get_daily_events

    try:
        events = await get_daily_events()
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
CHORE_NAME, CHORE_FREQ, CHORE_ASSIGNED = range(3)


@authorized_only
async def cmd_addchore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /addchore — start chore creation conversation."""
    await update.message.reply_text("What's the chore name? (e.g., 'Take out trash')")
    return CHORE_NAME


async def addchore_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive chore name, ask for frequency."""
    context.user_data["chore_name"] = update.message.text.strip()
    await update.message.reply_text("How often (in days)? (e.g., 7 for weekly)")
    return CHORE_FREQ


async def addchore_freq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive frequency, ask for assigned person."""
    text = update.message.text.strip()
    try:
        freq = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a number (e.g., 7 for weekly).")
        return CHORE_FREQ
    context.user_data["chore_freq"] = freq
    await update.message.reply_text("Who is it assigned to?")
    return CHORE_ASSIGNED


async def addchore_assigned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive assigned person, create the chore."""
    from src.data.db import ChoreDB

    assigned = update.message.text.strip()
    name = context.user_data.pop("chore_name")
    freq = context.user_data.pop("chore_freq")

    try:
        db = ChoreDB()
        chore = db.add_chore(name=name, frequency_days=freq, assigned_to=assigned)
        await update.message.reply_text(
            f"✅ Chore added: *{chore.name}* (every {chore.frequency_days} days, "
            f"assigned to {chore.assigned_to})",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to add chore: %s", exc)
        await update.message.reply_text("Sorry, couldn't save the chore. Please try again.")

    return ConversationHandler.END


async def addchore_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel chore creation."""
    context.user_data.pop("chore_name", None)
    context.user_data.pop("chore_freq", None)
    await update.message.reply_text("Chore creation cancelled.")
    return ConversationHandler.END


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


def build_app() -> Application:
    """Build and configure the Telegram Application with all handlers."""
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("chores", cmd_chores))
    app.add_handler(CommandHandler("done", cmd_done))

    # /addchore conversation handler
    addchore_conv = ConversationHandler(
        entry_points=[CommandHandler("addchore", cmd_addchore)],
        states={
            CHORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_name)],
            CHORE_FREQ: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_freq)],
            CHORE_ASSIGNED: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_assigned)],
        },
        fallbacks=[CommandHandler("cancel", addchore_cancel)],
    )
    app.add_handler(addchore_conv)

    # Text messages (non-command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Voice messages
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Morning Briefing scheduler (Phase 4)
    from src.core.scheduler import setup_scheduler
    setup_scheduler(app)

    logger.info("Telegram bot application built with %d handlers", len(app.handlers[0]))
    return app


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
