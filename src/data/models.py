"""
LifeOS Assistant — Data Models.

The Memory pillar: chores persist in SQLite across days, surviving bot restarts.
Unlike calendar events (which live in Google Calendar), chores are local state
that only LifeOS manages.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class User:
    """A registered bot user with their own calendar and data."""

    telegram_user_id: int
    display_name: str
    calendar_token_json: str | None = None
    onboarded: bool = False
    invited_by: int | None = None
    is_admin: bool = False
    created_at: str = ""


@dataclass
class Contact:
    """A named contact for smart guest resolution.

    When a user mentions a person's name in an event (e.g., "meeting with Yahav"),
    the system looks up the name in the contacts DB to find the email address.
    """

    id: int
    name: str              # original name, e.g. "Yahav"
    email: str             # e.g. "yahav@gmail.com"
    name_normalized: str   # lowercased for case-insensitive lookup
    user_id: int | None = None


@dataclass
class Chore:
    """A recurring chore tracked by LifeOS.

    Added exclusively via the /addchore Telegram command
    (never from free-text parsing).
    """

    id: int
    name: str                         # e.g., "Take out trash"
    frequency_days: int               # how often in days (7 = weekly)
    duration_minutes: int             # how long the chore takes
    preferred_time_start: str         # e.g. "17:00"
    preferred_time_end: str           # e.g. "21:00"
    next_due: str                     # ISO date YYYY-MM-DD
    assigned_to: str                  # person responsible
    last_done: str | None = None      # ISO date YYYY-MM-DD, None if never done
    calendar_event_id: str | None = None  # Google Calendar recurring event ID
    active: bool = field(default=True)
    user_id: int | None = None
