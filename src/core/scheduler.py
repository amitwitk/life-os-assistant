"""
LifeOS Assistant — Morning Briefing Scheduler.

The Morning Briefing pillar: a proactive daily push at 08:00 Asia/Jerusalem.
The user starts their day with a friendly, Claude-written summary of
today's calendar events and due chores — without needing to ask.
"""

from __future__ import annotations

import logging
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import anthropic
from telegram.ext import Application, ContextTypes

from src.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Morning summary generator
# ---------------------------------------------------------------------------


async def send_morning_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: send the morning briefing to all allowed users."""
    bot = context.bot

    for chat_id in settings.ALLOWED_USER_IDS:
        try:
            summary = await _build_morning_summary()
            await bot.send_message(chat_id=chat_id, text=summary)
            logger.info("Morning briefing sent to user %d", chat_id)
        except Exception as exc:
            logger.error("Failed to send morning briefing to %d: %s", chat_id, exc)


async def _build_morning_summary() -> str:
    """Gather calendar events + chores, then ask Claude for a friendly summary.

    Graceful degradation:
    - Calendar API fails → still include chores
    - DB fails → still include calendar
    - Both fail → fallback message
    - Claude fails → raw text formatting
    """
    events_text = ""
    chores_text = ""

    # 1. Calendar events
    try:
        from src.integrations.gcal_service import get_daily_events

        events = await get_daily_events()
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
        chores = db.get_due_chores()
        if chores:
            lines = [f"  - {c.name} (assigned to {c.assigned_to})" for c in chores]
            chores_text = "Chores due today:\n" + "\n".join(lines)
        else:
            chores_text = "Chores due today: None"
    except Exception as exc:
        logger.warning("Morning briefing: chore fetch failed: %s", exc)
        chores_text = "Chores due today: (unavailable)"

    raw_data = f"{events_text}\n\n{chores_text}"

    # 3. Both unavailable → fallback
    if "(unavailable)" in events_text and "(unavailable)" in chores_text:
        return (
            "בוקר טוב! ☀️\n\n"
            "לא הצלחתי לטעון את לוח הזמנים והמטלות הבוקר. "
            "נסה /today או /chores כדי לבדוק ידנית."
        )

    # 4. Claude summarization
    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=(
                "You are a friendly personal assistant. "
                "Summarize the following calendar events and chores into a warm, "
                "concise morning briefing in Hebrew. Use emoji sparingly. "
                "If there are no events or chores, say so cheerfully. "
                "Keep it under 300 words."
            ),
            messages=[{"role": "user", "content": raw_data}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Morning briefing: Claude summarization failed: %s", exc)
        # Fallback: raw text
        return f"בוקר טוב! ☀️\n\n{raw_data}"


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------


def setup_scheduler(app: Application) -> None:
    """Register the daily morning briefing job at 08:00 Asia/Jerusalem."""
    tz = ZoneInfo(settings.TIMEZONE)
    briefing_time = dt_time(hour=settings.MORNING_BRIEFING_HOUR, minute=0, tzinfo=tz)

    job_queue = app.job_queue
    job_queue.run_daily(
        send_morning_summary,
        time=briefing_time,
        name="morning_briefing",
    )

    logger.info(
        "Morning briefing scheduled at %02d:00 %s",
        settings.MORNING_BRIEFING_HOUR,
        settings.TIMEZONE,
    )
