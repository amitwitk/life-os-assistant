"""Telegram notification adapter — implements NotificationPort.

Wraps a telegram.Bot instance to satisfy the NotificationPort protocol.
"""

from __future__ import annotations

import logging

from telegram import Bot

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram implementation of NotificationPort."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(self, user_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=user_id, text=text)
