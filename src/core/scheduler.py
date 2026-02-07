"""
LifeOS Assistant — Morning Briefing Scheduler.

The Morning Briefing pillar: a proactive daily push at 08:00 Asia/Jerusalem.
The user starts their day with a friendly, LLM-written summary of
today's calendar events and due chores — without needing to ask.

This module is provider-agnostic: it depends on CalendarPort and
NotificationPort protocols, not on specific implementations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import settings
from src.core.llm import complete

if TYPE_CHECKING:
    from src.ports.calendar_port import CalendarPort
    from src.ports.notification_port import NotificationPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Morning summary generator
# ---------------------------------------------------------------------------


async def send_morning_summary(
    notifier: NotificationPort,
    user_db: object | None = None,
    calendar: CalendarPort | None = None,
) -> None:
    """Send the morning briefing to all users via the notifier.

    Multi-user mode (user_db provided): iterates over onboarded users,
    creates per-user calendar adapter from their stored credentials.

    Legacy mode (user_db is None, calendar provided): sends a single
    shared-calendar briefing to all ALLOWED_USER_IDS.
    """
    if user_db is not None:
        from src.adapters.calendar_factory import create_calendar_adapter

        users = user_db.list_users()
        for user in users:
            if not user.onboarded:
                continue
            try:
                user_cal = create_calendar_adapter(token_json=user.calendar_token_json)
                summary = await _build_morning_summary(
                    user_cal, user_id=user.telegram_user_id,
                )
                await notifier.send_message(user.telegram_user_id, summary)
                logger.info("Morning briefing sent to user %d", user.telegram_user_id)
            except Exception as exc:
                logger.error(
                    "Failed to send morning briefing to %d: %s",
                    user.telegram_user_id, exc,
                )
    else:
        # Legacy single-calendar mode
        for chat_id in settings.ALLOWED_USER_IDS:
            try:
                summary = await _build_morning_summary(calendar)
                await notifier.send_message(chat_id, summary)
                logger.info("Morning briefing sent to user %d", chat_id)
            except Exception as exc:
                logger.error("Failed to send morning briefing to %d: %s", chat_id, exc)


async def _build_morning_summary(
    calendar: CalendarPort,
    user_id: int | None = None,
) -> str:
    """Gather calendar events + chores, then ask the LLM for a friendly summary.

    Args:
        calendar: Calendar adapter (per-user or shared).
        user_id: If provided, scope chore queries to this user.

    Graceful degradation:
    - Calendar API fails -> still include chores
    - DB fails -> still include calendar
    - Both fail -> fallback message
    - LLM fails -> raw text formatting
    """
    events_text = ""
    chores_text = ""

    # 1. Calendar events
    try:
        events = await calendar.get_daily_events()
        if events:
            lines = []
            for ev in events:
                start = ev.get("start_time", "")
                if "T" in start:
                    start = start.split("T")[1][:5]
                end = ev.get("end_time", "")
                if "T" in end:
                    end = end.split("T")[1][:5]
                summary = ev.get("summary", "(no title)")
                lines.append(f"  {start}-{end} {summary}")
            events_text = "Calendar events today:\n" + "\n".join(lines)
        else:
            events_text = "Calendar events today: None"
    except Exception as exc:
        logger.warning("Morning briefing: calendar fetch failed: %s", exc)
        events_text = "Calendar events today: (unavailable)"

    # 2. Due chores
    try:
        from src.data.db import ChoreDB

        db = ChoreDB()
        chores = db.get_due_chores(user_id=user_id)
        if chores:
            lines = [f"  - {c.name} (assigned to {c.assigned_to})" for c in chores]
            chores_text = "Chores due today:\n" + "\n".join(lines)
        else:
            chores_text = "Chores due today: None"
    except Exception as exc:
        logger.warning("Morning briefing: chore fetch failed: %s", exc)
        chores_text = "Chores due today: (unavailable)"

    raw_data = f"{events_text}\n\n{chores_text}"

    # 3. Both unavailable -> fallback
    if "(unavailable)" in events_text and "(unavailable)" in chores_text:
        return (
            "בוקר טוב! ☀️\n\n"
            "לא הצלחתי לטעון את לוח הזמנים והמטלות הבוקר. "
            "נסה /today או /chores כדי לבדוק ידנית."
        )

    # 4. LLM summarization
    try:
        summary = await complete(
            system=(
                "You are a friendly personal assistant. "
                "Summarize the following calendar events and chores into a warm, "
                "concise morning briefing in Hebrew. Use emoji sparingly. "
                "If there are no events or chores, say so cheerfully. "
                "Keep it under 300 words."
            ),
            user_message=raw_data,
            max_tokens=512,
        )
        return summary.strip()
    except Exception as exc:
        logger.warning("Morning briefing: LLM summarization failed: %s", exc)
        # Fallback: raw text
        return f"בוקר טוב! ☀️\n\n{raw_data}"
