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


class RescheduleEvent(BaseModel):
    """Structured reschedule request extracted from natural language.

    JSON example:
    {
        "intent": "reschedule",
        "event_summary": "Meeting with Amit",
        "original_date": "2025-02-14",
        "new_time": "15:00"
    }
    """
    intent: str = "reschedule"
    event_summary: str
    original_date: str  # ISO format YYYY-MM-DD
    new_time: str       # HH:MM in 24h format


class QueryEvents(BaseModel):
    """Structured query request to view events on a date.

    JSON example:
    {
        "intent": "query",
        "date": "2025-02-14"
    }
    """
    intent: str = "query"
    date: str  # ISO format YYYY-MM-DD


ParserResponse = ParsedEvent | CancelEvent | RescheduleEvent | QueryEvents


# ---------------------------------------------------------------------------
# System prompt for LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an event extraction engine for a personal assistant.
Your job: parse the user's natural language message and extract calendar event details.
The user can either CREATE a new event, CANCEL an existing one, RESCHEDULE an existing one, or QUERY their schedule.

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

**Function 3: Reschedule Event**
If the user's message contains keywords like "reschedule", "move", "change time", "לדחות", "להזיז", use this JSON schema:
{{"intent": "reschedule", "event_summary": "string", "original_date": "YYYY-MM-DD", "new_time": "HH:MM"}}

- "event_summary" = the name/summary of the event to reschedule.
- "original_date" = the original date of the event.
- "new_time" = the new time for the event in 24-hour format.

**Function 4: Query Events**
If the user's message is asking about their schedule, what meetings they have, what's planned, etc. — keywords like "what meetings", "what do I have", "show me", "מה יש לי", "מה התוכניות", use this JSON schema:
{{"intent": "query", "date": "YYYY-MM-DD"}}

- "date" = the date the user is asking about. Interpret relative dates relative to today.
- If the user says "today", "this week", or doesn't specify a date, default to today's date.

**General Rules:**
- Support both Hebrew and English input.
- Always return valid JSON matching one of the schemas above.
- If the message does NOT contain any actionable event information (neither create, cancel, reschedule, nor query), return exactly: null
- Return ONLY the JSON object (or null). No markdown, no explanation, no extra text.
"""


# ---------------------------------------------------------------------------
# Response Cleaning Functions
# ---------------------------------------------------------------------------

def _clean_llm_response(raw_text: str) -> str:
    """Remove markdown code block delimiters from LLM's raw response."""
    cleaned_text = raw_text.strip()
    if cleaned_text.startswith("```json"):
        cleaned_text = cleaned_text.removeprefix("```json")
    if cleaned_text.endswith("```"):
        cleaned_text = cleaned_text.removesuffix("```")
    return cleaned_text.strip()


# ---------------------------------------------------------------------------
# Error Handling Functions
# ---------------------------------------------------------------------------

def _handle_json_decode_error(exc: json.JSONDecodeError, raw_text: str) -> None:
    """Log and handle JSON decoding errors from LLM response."""
    logger.error("Failed to parse LLM response as JSON: %s — raw: '%s'", exc, raw_text)

def _handle_unknown_intent(intent: str) -> None:
    """Log and handle cases where LLM returns an unknown intent."""
    logger.warning("LLM returned unknown intent: '%s'", intent)

def _handle_generic_parser_error(exc: Exception) -> None:
    """Log and handle any unexpected errors during message parsing."""
    logger.error("Unexpected error in parse_message: %s", exc)


# ---------------------------------------------------------------------------
# Parser function
# ---------------------------------------------------------------------------

_MATCH_PROMPT = """\
You are an event matching engine. The user wants to act on a calendar event.
They described it as: "{user_description}"

Here are the actual events on their calendar for that date:
{events_list}

Which event (if any) is the user referring to? Consider:
- The user's wording may differ from the actual event name (e.g., "Amit's meeting" = "meeting with amit")
- Ignore typos, casing, word order differences
- Match by meaning, not exact text

Return ONLY the index number (0-based) of the matching event, or "none" if no event matches.
No explanation, no extra text — just the number or "none".
"""


async def match_event(user_description: str, events: list[dict]) -> dict | None:
    """Use the LLM to fuzzy-match a user's event description against actual calendar events.

    Returns the matched event dict, or None if no match found.
    """
    if not events:
        return None

    events_list = "\n".join(
        f"{i}. {ev.get('summary', '(no title)')}" for i, ev in enumerate(events)
    )

    try:
        raw = await complete(
            system=_MATCH_PROMPT.format(
                user_description=user_description,
                events_list=events_list,
            ),
            user_message="Which event index matches?",
            max_tokens=16,
        )
        raw = raw.strip().lower()
        logger.debug("LLM match response: %s", raw)

        if raw == "none":
            return None

        index = int(raw)
        if 0 <= index < len(events):
            logger.info("Matched '%s' → '%s'", user_description, events[index].get("summary"))
            return events[index]

        logger.warning("LLM returned out-of-range index: %s", raw)
        return None
    except (ValueError, IndexError):
        logger.warning("LLM match response not a valid index: '%s'", raw)
        return None
    except Exception as exc:
        logger.error("Error in match_event: %s", exc)
        return None


async def parse_message(user_message: str) -> ParserResponse | None:
    """Parse a user message into a structured calendar event using the configured LLM.

    Returns ParsedEvent, CancelEvent, RescheduleEvent, QueryEvents, or None.
    """
    system_prompt = _SYSTEM_PROMPT.format(today=date.today().isoformat())

    try:
        raw_text = await complete(
            system=system_prompt,
            user_message=user_message,
            max_tokens=256,
        )
        raw_text = _clean_llm_response(raw_text)
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
        if intent == "reschedule":
            parsed = RescheduleEvent(**data)
            logger.info("Parsed event reschedule: %s on %s to %s", parsed.event_summary, parsed.original_date, parsed.new_time)
            return parsed
        if intent == "query":
            parsed = QueryEvents(**data)
            logger.info("Parsed event query for %s", parsed.date)
            return parsed

        _handle_unknown_intent(intent)
        return None

    except json.JSONDecodeError as exc:
        _handle_json_decode_error(exc, raw_text)
        return None
    except Exception as exc:
        _handle_generic_parser_error(exc)
        return None

