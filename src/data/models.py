"""
LifeOS Assistant — Data Models.

The Memory pillar: chores persist in SQLite across days, surviving bot restarts.
Unlike calendar events (which live in Google Calendar), chores are local state
that only LifeOS manages.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chore:
    """A recurring chore tracked by LifeOS.

    Added exclusively via the /addchore Telegram command
    (never from free-text parsing).
    """

    id: int
    name: str                         # e.g., "Take out trash"
    frequency_days: int               # how often in days (7 = weekly)
    next_due: str                     # ISO date YYYY-MM-DD
    assigned_to: str                  # person responsible
    last_done: str | None = None      # ISO date YYYY-MM-DD, None if never done
    active: bool = field(default=True)
