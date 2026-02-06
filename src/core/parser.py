"""
LifeOS Assistant — LLM Parser.

Brain of the Capture System: converts natural language (Hebrew/English)
into structured calendar events using the configured LLM provider.

The parser ALWAYS outputs a calendar event. There is no intent routing.
Chores are added only via the explicit /addchore command.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from pydantic import BaseModel

from src.core.llm import complete

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared JSON contract — consumed by 2.2_Event_Writer and 3.3_Main_Logic
# ---------------------------------------------------------------------------

class ParsedEvent(BaseModel):
    """Structured calendar event extracted from natural language.

    JSON example:
    {
        "intent": "create",
        "event": "Dentist appointment",
        "date": "2025-02-14",
        "time": "16:00",
        "duration_minutes": 60,
        "description": ""
    }
    """
    intent: str = "create"
    event: str
    date: str          # ISO format YYYY-MM-DD
    time: str          # HH:MM in 24h format
    duration_minutes: int = 60
    description: str = ""


class CancelEvent(BaseModel):
    """Structured cancellation request extracted from natural language.

    JSON example:
    {
        "intent": "cancel",
        "event_summary": "Dentist appointment",
        "date": "2025-02-14"
    }
    """
    intent: str = "cancel"
    event_summary: str
    date: str  # ISO format YYYY-MM-DD


ParserResponse = ParsedEvent | CancelEvent


# ---------------------------------------------------------------------------
# System prompt for LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an event extraction engine for a personal assistant.
Your job: parse the user's natural language message and extract calendar event details.
The user can either CREATE a new event or CANCEL an existing one.

Today's date is {today}.

**Function 1: Create Event**
If the user wants to schedule something, use this JSON schema:
{{"intent": "create", "event": "string", "date": "YYYY-MM-DD", "time": "HH:MM", "duration_minutes": integer, "description": "string"}}

- "event" = short title for the calendar event.
- "time" must be in 24-hour format.
- "duration_minutes" defaults to 60 if not mentioned.
- Interpret relative dates ("tomorrow", "next Monday") relative to today.

**Function 2: Cancel Event**
If the user's message contains keywords like "cancel", "delete", "remove", "ביטול", "בטל", "למחיקה", use this JSON schema:
{{"intent": "cancel", "event_summary": "string", "date": "YYYY-MM-DD"}}

- "event_summary" = the name/summary of the event to cancel.
- "date" = the date of the event to cancel.

**General Rules:**
- Support both Hebrew and English input.
- Always return valid JSON matching one of the schemas above.
- If the message does NOT contain any actionable event information (neither create nor cancel), return exactly: null
- Return ONLY the JSON object (or null). No markdown, no explanation, no extra text.
"""


# ---------------------------------------------------------------------------
# Parser function
# ---------------------------------------------------------------------------

async def parse_message(user_message: str) -> ParserResponse | None:
    """Parse a user message into a structured calendar event using the configured LLM.

    Returns ParsedEvent, CancelEvent, or None.
    """
    system_prompt = _SYSTEM_PROMPT.format(today=date.today().isoformat())

    try:
        raw_text = await complete(
            system=system_prompt,
            user_message=user_message,
            max_tokens=256,
        )
        raw_text = raw_text.strip()
        logger.debug("LLM raw response: %s", raw_text)

        if raw_text == "null" or not raw_text:
            logger.info("No event found in message: %s", user_message[:80])
            return None

        data = json.loads(raw_text)
        intent = data.get("intent")

        if intent == "create":
            parsed = ParsedEvent(**data)
            logger.info("Parsed event creation: %s on %s at %s", parsed.event, parsed.date, parsed.time)
            return parsed
        if intent == "cancel":
            parsed = CancelEvent(**data)
            logger.info("Parsed event cancellation: %s on %s", parsed.event_summary, parsed.date)
            return parsed

        logger.warning("LLM returned unknown intent: '%s'", intent)
        return None

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM response as JSON: %s — raw: %s", exc, raw_text)
        return None
    except Exception as exc:
        logger.error("Unexpected error in parse_message: %s", exc)
        return None
