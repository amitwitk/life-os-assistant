"""
LifeOS Assistant — Telegram Bot.

Telegram is the only user interface — the single gateway to LifeOS.
Every interaction (text capture, voice capture, chore management, daily
briefings) flows through this bot.

Security-first: unauthorized users are silently ignored.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Coroutine

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
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
    """Handle /today — placeholder (wired in 3.3)."""
    await update.message.reply_text("⏳ Feature coming soon.")


@authorized_only
async def cmd_addchore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /addchore — placeholder (wired in 3.3)."""
    await update.message.reply_text("⏳ Feature coming soon.")


@authorized_only
async def cmd_chores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /chores — placeholder (wired in 3.3)."""
    await update.message.reply_text("⏳ Feature coming soon.")


@authorized_only
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done — placeholder (wired in 3.3)."""
    await update.message.reply_text("⏳ Feature coming soon.")


@authorized_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages — echo for now (replaced in 3.3)."""
    await update.message.reply_text(f"[Echo]: {update.message.text}")


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
    app.add_handler(CommandHandler("addchore", cmd_addchore))
    app.add_handler(CommandHandler("chores", cmd_chores))
    app.add_handler(CommandHandler("done", cmd_done))

    # Text messages (non-command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

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
