"""
LifeOS Assistant — LLM Parser.

Brain of the Capture System: converts natural language (Hebrew/English)
into structured calendar events using Anthropic Claude.

The parser ALWAYS outputs a calendar event. There is no intent routing.
Chores are added only via the explicit /addchore command.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared JSON contract — consumed by 2.2_Event_Writer and 3.3_Main_Logic
# ---------------------------------------------------------------------------

class ParsedEvent(BaseModel):
    """Structured calendar event extracted from natural language.

    JSON example:
    {
        "event": "Dentist appointment",
        "date": "2025-02-14",
        "time": "16:00",
        "duration_minutes": 60,
        "description": ""
    }
    """
    event: str
    date: str          # ISO format YYYY-MM-DD
    time: str          # HH:MM in 24h format
    duration_minutes: int = 60
    description: str = ""


# ---------------------------------------------------------------------------
# System prompt for Claude
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an event extraction engine for a personal assistant.
Your job: parse the user's natural language message and extract calendar event details.

Rules:
- Today's date is {today}.
- Interpret relative dates ("tomorrow", "next Monday", "יום שלישי הבא") relative to today.
- Support both Hebrew and English input.
- Always return valid JSON matching this exact schema:
  {{"event": "string", "date": "YYYY-MM-DD", "time": "HH:MM", "duration_minutes": integer, "description": "string"}}
- "event" = short title for the calendar event.
- "time" must be in 24-hour format.
- "duration_minutes" defaults to 60 if not mentioned.
- "description" is optional extra context; default to empty string.
- If the message does NOT contain any event information, return exactly: null
- Return ONLY the JSON object (or null). No markdown, no explanation, no extra text.
"""


# ---------------------------------------------------------------------------
# Parser function
# ---------------------------------------------------------------------------

async def parse_message(user_message: str) -> ParsedEvent | None:
    """Parse a user message into a structured calendar event using Claude.

    Returns ParsedEvent if an event was found, None otherwise.
    """
    # Lazy import to avoid triggering config validation at module load time
    # when running tests with mocked env vars.
    from src.config import settings

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    system_prompt = _SYSTEM_PROMPT.format(today=date.today().isoformat())

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()
        logger.debug("Claude raw response: %s", raw_text)

        if raw_text == "null" or not raw_text:
            logger.info("No event found in message: %s", user_message[:80])
            return None

        data = json.loads(raw_text)
        parsed = ParsedEvent(**data)
        logger.info("Parsed event: %s on %s at %s", parsed.event, parsed.date, parsed.time)
        return parsed

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude response as JSON: %s — raw: %s", exc, raw_text)
        return None
    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error in parse_message: %s", exc)
        return None
