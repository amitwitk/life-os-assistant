"""Notification port — abstract interface for sending messages to users.

Core modules depend on this protocol, never on a specific messaging provider.
"""

from __future__ import annotations

from typing import Protocol


class NotificationPort(Protocol):
    """Abstract notification interface used by core modules."""

    async def send_message(self, user_id: int, text: str) -> None: ...
