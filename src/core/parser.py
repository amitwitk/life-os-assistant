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

from pydantic import BaseModel, Field

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
        "description": "",
        "guests": [],
        "mentioned_contacts": []
    }
    """
    intent: str = Field(default="create", description="Action type — always 'create'")
    event: str = Field(description="Short title for the calendar event")
    date: str = Field(description="Event date in YYYY-MM-DD format")
    time: str = Field(default="", description="Start time in HH:MM 24h format. Empty string if not specified")
    duration_minutes: int = Field(default=60, description="Duration in minutes. Defaults to 60")
    description: str = Field(default="", description="Optional event description")
    guests: list[str] = Field(default_factory=list, description="Explicit email addresses to invite")
    mentioned_contacts: list[str] = Field(default_factory=list, description="Person names mentioned as participants, to resolve via contacts DB")
    location: str = Field(default="", description="Physical venue, address, or place name. Empty string if not specified")
    maps_url: str = Field(default="", json_schema_extra={"prompt_hidden": True})

    @property
    def log_summary(self) -> str:
        return f"event creation: {self.event} on {self.date} at {self.time}"


class CancelEvent(BaseModel):
    """Structured cancellation request extracted from natural language.

    JSON example:
    {
        "intent": "cancel",
        "event_summary": "Dentist appointment",
        "date": "2025-02-14"
    }
    """
    intent: str = Field(default="cancel", description="Action type — always 'cancel'")
    event_summary: str = Field(description="Name/summary of the event to cancel")
    date: str = Field(description="Date of the event to cancel in YYYY-MM-DD format")

    @property
    def log_summary(self) -> str:
        return f"event cancellation: {self.event_summary} on {self.date}"


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
    intent: str = Field(default="reschedule", description="Action type — always 'reschedule'")
    event_summary: str = Field(description="Name/summary of the event to reschedule")
    original_date: str = Field(description="Original date of the event in YYYY-MM-DD format")
    new_time: str = Field(description="New time for the event in HH:MM 24h format")

    @property
    def log_summary(self) -> str:
        return f"event reschedule: {self.event_summary} on {self.original_date} to {self.new_time}"


class QueryEvents(BaseModel):
    """Structured query request to view events on a date.

    JSON example:
    {
        "intent": "query",
        "date": "2025-02-14"
    }
    """
    intent: str = Field(default="query", description="Action type — always 'query'")
    date: str = Field(description="Date to query in YYYY-MM-DD format")

    @property
    def log_summary(self) -> str:
        return f"event query for {self.date}"


class CancelAllExcept(BaseModel):
    """Cancel all events on a date except specified ones.

    JSON example:
    {
        "intent": "cancel_all_except",
        "date": "2025-02-14",
        "exceptions": ["Padel game"]
    }
    """
    intent: str = Field(default="cancel_all_except", description="Action type — always 'cancel_all_except'")
    date: str = Field(description="Date of the events in YYYY-MM-DD format")
    exceptions: list[str] = Field(description="Event descriptions to KEEP (not cancel)")

    @property
    def log_summary(self) -> str:
        return f"cancel-all-except on {self.date}, keeping: {self.exceptions}"


class AddGuests(BaseModel):
    """Add guests to an existing calendar event.

    JSON example:
    {
        "intent": "add_guests",
        "event_summary": "Meeting with Dan",
        "date": "2025-02-14",
        "guests": ["dan@email.com"]
    }
    """
    intent: str = Field(default="add_guests", description="Action type — always 'add_guests'")
    event_summary: str = Field(description="Name/summary of the existing event")
    date: str = Field(description="Date of the existing event in YYYY-MM-DD format")
    guests: list[str] = Field(description="Email addresses to add as guests")

    @property
    def log_summary(self) -> str:
        return f"add-guests to '{self.event_summary}' on {self.date}: {self.guests}"


ParserResponse = ParsedEvent | CancelEvent | RescheduleEvent | QueryEvents | CancelAllExcept | AddGuests

# ---------------------------------------------------------------------------
# Intent registry — maps intent string to model class
# ---------------------------------------------------------------------------

INTENT_REGISTRY: dict[str, type[BaseModel]] = {
    "create": ParsedEvent,
    "cancel": CancelEvent,
    "reschedule": RescheduleEvent,
    "query": QueryEvents,
    "cancel_all_except": CancelAllExcept,
    "add_guests": AddGuests,
}


# ---------------------------------------------------------------------------
# System prompt for LLM — schema-driven generation
# ---------------------------------------------------------------------------

_INTENT_LABELS: dict[str, str] = {
    "create": "Create Event",
    "cancel": "Cancel Event",
    "reschedule": "Reschedule Event",
    "query": "Query Events",
    "cancel_all_except": "Cancel All Except",
    "add_guests": "Add Guests",
}

_INTENT_TRIGGERS: dict[str, str] = {
    "create": 'If the user wants to schedule something',
    "cancel": 'If the user\'s message contains keywords like "cancel", "delete", "remove", "ביטול", "בטל", "למחיקה"',
    "reschedule": 'If the user\'s message contains keywords like "reschedule", "move", "change time", "לדחות", "להזיז"',
    "query": 'If the user\'s message is asking about their schedule, what meetings they have, what\'s planned, etc. — keywords like "what meetings", "what do I have", "show me", "מה יש לי", "מה התוכניות"',
    "cancel_all_except": 'If the user wants to cancel ALL events on a date EXCEPT specific ones (e.g., "cancel everything today except the padel game")',
    "add_guests": 'If the user wants to add guests/invitees to an EXISTING event (e.g., "Add dan@email.com to the meeting with Dan tomorrow")',
}

_BEHAVIORAL_RULES: dict[str, list[str]] = {
    "create": [
        '"time" must be in 24-hour format. If the user does NOT specify a time, return "time": "".',
        'This includes cases where the user asks when they are free or available (e.g., "meeting with Shon today", "אני רוצה להיפגש עם שון היום מתי אני פנוי", "I want to meet Dan tomorrow, when am I available?"). These are CREATE intents with "time": "", NOT query intents.',
        'Interpret relative dates ("tomorrow", "next Monday") relative to today.',
        '"mentioned_contacts" = list of people\'s NAMES mentioned as participants.\n  e.g., "meeting with Yahav and Dan" → ["Yahav", "Dan"]. Default to [].\n  Only include actual person names, not generic descriptions like "the team".',
        'Only extract "location" if the user mentions a specific place (e.g., "at Blue Bottle Coffee", "in the office"). Default to "".',
    ],
    "cancel": [
        'If the user mentions canceling MULTIPLE specific events, return a separate cancel object for each.',
    ],
    "query": [
        'Interpret relative dates relative to today.',
        'If the user says "today", "this week", or doesn\'t specify a date, default to today\'s date.',
    ],
    "add_guests": [
        "Only use this when the user explicitly asks to add guests to an EXISTING event. If they're creating a new event with guests, use Function 1 with the \"guests\" field instead.",
    ],
}


def _is_prompt_hidden(field_info: object) -> bool:
    """Check if a field is marked as prompt_hidden via json_schema_extra."""
    extra = field_info.json_schema_extra
    if isinstance(extra, dict):
        return extra.get("prompt_hidden", False)
    return False


def _generate_schema_line(intent: str, model_cls: type[BaseModel]) -> str:
    """Auto-generate the JSON schema example line from a Pydantic model."""
    parts: list[str] = []
    for name, field_info in model_cls.model_fields.items():
        if _is_prompt_hidden(field_info):
            continue
        annotation = field_info.annotation
        # Determine placeholder value based on type
        if name == "intent":
            parts.append(f'"{name}": "{intent}"')
        elif annotation is str or (hasattr(annotation, '__origin__') is False and annotation is str):
            parts.append(f'"{name}": "string"')
        elif annotation is int:
            parts.append(f'"{name}": integer')
        elif hasattr(annotation, '__origin__') and annotation.__origin__ is list:
            parts.append(f'"{name}": ["string"]')
        else:
            parts.append(f'"{name}": "string"')
    return "{{" + ", ".join(parts) + "}}"


def _generate_field_docs(model_cls: type[BaseModel]) -> list[str]:
    """Auto-generate field documentation lines from Field descriptions."""
    lines: list[str] = []
    for name, field_info in model_cls.model_fields.items():
        if name == "intent" or _is_prompt_hidden(field_info):
            continue
        desc = field_info.description or ""
        lines.append(f'- "{name}" = {desc}')
    return lines


def _generate_intent_section(intent: str, model_cls: type[BaseModel], number: int) -> str:
    """Generate a full prompt section for one intent."""
    label = _INTENT_LABELS.get(intent, intent.replace("_", " ").title())
    trigger = _INTENT_TRIGGERS.get(intent, "")
    schema_line = _generate_schema_line(intent, model_cls)
    field_docs = _generate_field_docs(model_cls)
    behavioral = _BEHAVIORAL_RULES.get(intent, [])

    lines = [f"**Function {number}: {label}**"]
    lines.append(f"{trigger}, use this JSON schema:")
    lines.append(schema_line)
    lines.append("")
    lines.extend(field_docs)
    for rule in behavioral:
        lines.append(f"- {rule}")
    return "\n".join(lines)


def _build_system_prompt() -> str:
    """Assemble the full system prompt from auto-generated and hand-crafted parts."""
    header = """\
You are an event extraction engine for a personal assistant.
Your job: parse the user's natural language message and extract ALL calendar actions.
A single message may contain multiple actions — extract every one of them.

Today's date is {today}.

**ALWAYS return a JSON array** `[]`, even for a single action.
"""

    sections: list[str] = []
    for number, (intent, model_cls) in enumerate(INTENT_REGISTRY.items(), start=1):
        sections.append(_generate_intent_section(intent, model_cls, number))

    general_rules = """\
**General Rules:**
- Support both Hebrew and English input.
- A single message may contain multiple actions. Extract ALL of them into the array.
- Always return a valid JSON array matching the schemas above.
- If the message does NOT contain any actionable event information, return exactly: []
- Return ONLY the JSON array (or []). No markdown, no explanation, no extra text."""

    return header + "\n" + "\n\n".join(sections) + "\n\n" + general_rules + "\n"


_SYSTEM_PROMPT = _build_system_prompt()


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
    model_cls = INTENT_REGISTRY.get(intent)

    if not model_cls:
        _handle_unknown_intent(intent)
        return None

    parsed = model_cls(**data)
    logger.info("Parsed %s", parsed.log_summary)
    return parsed


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

