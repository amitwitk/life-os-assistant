"""
LifeOS Assistant — Daily Schedulers.

Morning Briefing: a proactive daily push at 08:00 Asia/Jerusalem with
an LLM-written summary of today's calendar events and due chores.

Nightly Alarm: a 21:00 push recommending when to set the alarm for
tomorrow's first event, factoring in prep time and travel time.

This module is provider-agnostic: it depends on CalendarPort and
NotificationPort protocols, not on specific implementations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import settings
from src.core.llm import complete

if TYPE_CHECKING:
    from src.core.alarm_calculator import AlarmRecommendation
    from src.data.models import User
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


# ---------------------------------------------------------------------------
# Nightly alarm recommendation
# ---------------------------------------------------------------------------


async def send_nightly_alarm(
    notifier: NotificationPort,
    user_db: object | None = None,
) -> None:
    """Send nightly alarm recommendation to all onboarded users.

    Iterates over onboarded users, fetches tomorrow's events,
    finds the first timed event, and sends an alarm suggestion.
    """
    if user_db is None:
        return

    from src.adapters.calendar_factory import create_calendar_adapter

    users = user_db.list_users()
    for user in users:
        if not user.onboarded:
            continue
        try:
            user_cal = create_calendar_adapter(token_json=user.calendar_token_json)
            message = await _send_alarm_for_user(user, user_cal, notifier)
            if message:
                logger.info("Nightly alarm sent to user %d", user.telegram_user_id)
        except Exception as exc:
            logger.error(
                "Failed to send nightly alarm to %d: %s",
                user.telegram_user_id, exc,
            )


async def _send_alarm_for_user(
    user: User,
    calendar: CalendarPort,
    notifier: NotificationPort,
) -> str | None:
    """Fetch tomorrow's events, calculate alarm, send notification.

    Returns the message sent, or None if no notification was needed.
    """
    from datetime import date, timedelta

    from src.core.alarm_calculator import (
        build_alarm_recommendation,
        find_first_timed_event,
        is_late_start,
    )

    tomorrow = date.today() + timedelta(days=1)
    tomorrow_str = tomorrow.isoformat()

    events = await calendar.get_daily_events(target_date=tomorrow_str)
    if not events:
        return None

    first_event = find_first_timed_event(events)
    if first_event is None:
        return None

    # Try to get travel time
    travel_minutes, travel_text = await _get_travel_for_event(user, first_event)

    prep_minutes = settings.DEFAULT_PREP_TIME_MINUTES
    rec = build_alarm_recommendation(
        first_event, prep_minutes, travel_minutes, travel_text,
    )
    late = is_late_start(first_event["start_time"])

    message = _format_alarm_message(rec, late)
    await notifier.send_message(user.telegram_user_id, message)
    return message


async def _get_travel_for_event(
    user: User,
    event: dict,
) -> tuple[int | None, str | None]:
    """Look up travel time from user's home to event location.

    Returns (duration_minutes, display_text) or (None, None) on any failure.
    """
    home = getattr(user, "home_address", None)
    location = event.get("location")
    api_key = settings.GOOGLE_MAPS_API_KEY

    if not home or not location or not api_key:
        return None, None

    try:
        from src.integrations.google_maps import get_travel_time

        result = await get_travel_time(home, location, api_key)
        if result is None:
            return None, None
        return result.duration_minutes, f"{result.duration_text} ({result.distance_text})"
    except Exception as exc:
        logger.warning("Travel time lookup failed: %s", exc)
        return None, None


def _format_alarm_message(rec: AlarmRecommendation, late: bool) -> str:
    """Format a human-readable alarm notification message."""
    lines = [
        f"Set your alarm for *{rec.alarm_time}*",
        f"Tomorrow's first event: *{rec.event_summary}* at {rec.event_start}",
        f"Prep time: {rec.prep_minutes} min",
    ]

    if rec.travel_minutes is not None:
        lines.append(f"Travel: {rec.travel_text}")

    if late:
        lines.append("\nYour first event is after noon — enjoy a relaxed morning!")

    return "\n".join(lines)
