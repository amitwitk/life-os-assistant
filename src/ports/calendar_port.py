"""Calendar port — abstract interface for calendar operations.

Core modules depend on this protocol, never on a specific provider.
"""

from __future__ import annotations

from typing import Protocol

from src.core.parser import ParsedEvent


class CalendarError(Exception):
    """Raised when any calendar provider operation fails."""


class CalendarPort(Protocol):
    """Abstract calendar interface used by core modules."""

    async def add_event(self, parsed_event: ParsedEvent) -> dict: ...

    async def find_events(
        self, query: str | None = None, target_date: str | None = None
    ) -> list[dict]: ...

    async def delete_event(self, event_id: str) -> None: ...

    async def update_event(
        self, event_id: str, new_date: str, new_time: str
    ) -> dict: ...

    async def add_recurring_event(
        self,
        summary: str,
        description: str,
        start_date: str,
        start_time: str,
        end_time: str,
        frequency_days: int,
        occurrences: int,
    ) -> dict: ...

    async def get_daily_events(
        self, target_date: str | None = None
    ) -> list[dict]: ...

    async def add_guests(
        self, event_id: str, guests: list[str]
    ) -> dict: ...
