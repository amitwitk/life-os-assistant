"""
LifeOS Assistant — LLM Parser.

Brain of the Capture System: converts natural language (Hebrew/English)
into structured calendar actions using the configured LLM provider.

A single message may produce multiple actions (create, cancel, reschedule,
query, cancel-all-except). Chores are added only via the explicit /addchore
command.
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


class CancelAllExcept(BaseModel):
    """Cancel all events on a date except specified ones.

    JSON example:
    {
        "intent": "cancel_all_except",
        "date": "2025-02-14",
        "exceptions": ["Padel game"]
    }
    """
    intent: str = "cancel_all_except"
    date: str          # ISO format YYYY-MM-DD
    exceptions: list[str]  # event descriptions to KEEP


ParserResponse = ParsedEvent | CancelEvent | RescheduleEvent | QueryEvents | CancelAllExcept


# ---------------------------------------------------------------------------
# System prompt for LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an event extraction engine for a personal assistant.
Your job: parse the user's natural language message and extract ALL calendar actions.
A single message may contain multiple actions — extract every one of them.

Today's date is {today}.

**ALWAYS return a JSON array** `[]`, even for a single action.

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
- If the user mentions canceling MULTIPLE specific events, return a separate cancel object for each.

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

**Function 5: Cancel All Except**
If the user wants to cancel ALL events on a date EXCEPT specific ones (e.g., "cancel everything today except the padel game"), use this JSON schema:
{{"intent": "cancel_all_except", "date": "YYYY-MM-DD", "exceptions": ["event to keep 1", "event to keep 2"]}}

- "date" = the date of the events.
- "exceptions" = list of event descriptions that should NOT be canceled (the ones to keep).

**General Rules:**
- Support both Hebrew and English input.
- A single message may contain multiple actions. Extract ALL of them into the array.
- Always return a valid JSON array matching the schemas above.
- If the message does NOT contain any actionable event information, return exactly: []
- Return ONLY the JSON array (or []). No markdown, no explanation, no extra text.
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


def _instantiate_action(data: dict) -> ParserResponse | None:
    """Instantiate a single action dict into its typed model, or None if unknown."""
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
    if intent == "cancel_all_except":
        parsed = CancelAllExcept(**data)
        logger.info("Parsed cancel-all-except on %s, keeping: %s", parsed.date, parsed.exceptions)
        return parsed

    _handle_unknown_intent(intent)
    return None


async def parse_message(user_message: str) -> list[ParserResponse]:
    """Parse a user message into structured calendar actions using the configured LLM.

    Returns a list of ParserResponse objects (may be empty).
    """
    system_prompt = _SYSTEM_PROMPT.format(today=date.today().isoformat())

    try:
        raw_text = await complete(
            system=system_prompt,
            user_message=user_message,
            max_tokens=1024,
        )
        raw_text = _clean_llm_response(raw_text)
        logger.debug("LLM raw response: %s", raw_text)

        if raw_text in ("null", "[]", "") or not raw_text:
            logger.info("No actions found in message: %s", user_message[:80])
            return []

        data = json.loads(raw_text)

        # Defensive wrapping: if LLM returns a single dict instead of list
        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            logger.warning("LLM returned unexpected type: %s", type(data).__name__)
            return []

        results: list[ParserResponse] = []
        for item in data:
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict item in array: %s", item)
                continue
            action = _instantiate_action(item)
            if action is not None:
                results.append(action)

        return results

    except json.JSONDecodeError as exc:
        _handle_json_decode_error(exc, raw_text)
        return []
    except Exception as exc:
        _handle_generic_parser_error(exc)
        return []


# ---------------------------------------------------------------------------
# Batch event matching
# ---------------------------------------------------------------------------

_BATCH_MATCH_PROMPT = """\
You are an event matching engine. The user wants to act on multiple calendar events.

Here are the event descriptions the user mentioned:
{descriptions_list}

Here are the actual events on their calendar for that date:
{events_list}

For EACH user description (in order), return the 0-based index of the matching calendar event, or "none" if no event matches.
Consider: the user's wording may differ from the actual event name. Match by meaning, not exact text.

Return ONLY a JSON array of indices/none values. Example: [0, "none", 2]
No explanation, no extra text — just the JSON array.
"""


async def batch_match_events(
    descriptions: list[str], events: list[dict],
) -> list[dict | None]:
    """Use the LLM to fuzzy-match multiple event descriptions against calendar events in one call.

    Returns a list of matched event dicts (or None for unmatched) in the same order as descriptions.
    Falls back to sequential match_event() calls if the LLM response is malformed.
    """
    if not events or not descriptions:
        return [None] * len(descriptions)

    events_list = "\n".join(
        f"{i}. {ev.get('summary', '(no title)')}" for i, ev in enumerate(events)
    )
    descriptions_list = "\n".join(
        f"{i}. \"{desc}\"" for i, desc in enumerate(descriptions)
    )

    try:
        raw = await complete(
            system=_BATCH_MATCH_PROMPT.format(
                descriptions_list=descriptions_list,
                events_list=events_list,
            ),
            user_message="Which event indices match?",
            max_tokens=64,
        )
        raw = _clean_llm_response(raw)
        logger.debug("LLM batch match response: %s", raw)

        indices = json.loads(raw)
        if not isinstance(indices, list) or len(indices) != len(descriptions):
            raise ValueError(f"Expected list of length {len(descriptions)}, got {indices}")

        results: list[dict | None] = []
        for idx in indices:
            if idx == "none" or idx is None:
                results.append(None)
            elif isinstance(idx, int) and 0 <= idx < len(events):
                results.append(events[idx])
            else:
                results.append(None)

        return results

    except Exception as exc:
        logger.warning("Batch match failed (%s), falling back to sequential matching", exc)
        results = []
        for desc in descriptions:
            matched = await match_event(desc, events)
            results.append(matched)
        return results


async def batch_exclude_events(
    exceptions: list[str], events: list[dict],
) -> list[dict]:
    """Identify events to CANCEL by excluding the ones the user wants to keep.

    Args:
        exceptions: Event descriptions the user wants to KEEP.
        events: All calendar events on the date.

    Returns:
        Events that should be canceled (all events NOT matching any exception).
    """
    if not exceptions:
        return list(events)

    matched_keeps = await batch_match_events(exceptions, events)
    keep_ids = {ev["id"] for ev in matched_keeps if ev is not None}

    return [ev for ev in events if ev["id"] not in keep_ids]

