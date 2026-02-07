"""
LifeOS Assistant — UI-Agnostic Action Service.

Stateless service layer that orchestrates all business logic:
parse text -> check conflicts -> create/cancel/reschedule events -> return
structured response objects.

Each UI adapter (Telegram, web, Discord) calls this service and renders
the response objects in its own way.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from src.ports.calendar_port import CalendarError

if TYPE_CHECKING:
    from src.data.db import ContactDB
    from src.data.models import Chore
    from src.ports.calendar_port import CalendarPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class ResponseKind(Enum):
    SUCCESS = "success"
    ERROR = "error"
    CONFLICT_PROMPT = "conflict_prompt"
    CONTACT_PROMPT = "contact_prompt"
    BATCH_CANCEL_PROMPT = "batch_cancel_prompt"
    QUERY_RESULT = "query_result"
    BATCH_SUMMARY = "batch_summary"
    NO_ACTION = "no_action"
    SLOT_SUGGESTION = "slot_suggestion"


@dataclass
class EventInfo:
    summary: str
    date: str
    time: str
    link: str = ""
    location: str = ""
    maps_url: str = ""
    event_id: str = ""


@dataclass
class ConflictOption:
    key: str           # "suggested" | "force" | "custom" | "cancel"
    label: str         # "Use 15:30", "Force 14:00", etc.
    time: str | None = None


@dataclass
class PendingEvent:
    pending_type: str                      # "create" | "reschedule"
    parsed_event_json: dict | None = None  # ParsedEvent.model_dump()
    event_id: str | None = None
    date: str | None = None
    time: str | None = None
    duration: int | None = None
    summary: str | None = None


@dataclass
class PendingBatchCancel:
    events: list[dict] = field(default_factory=list)  # [{"id": str, "summary": str}]


@dataclass
class ActionResult:
    action_type: str   # "create" | "cancel" | "reschedule" | "query" | "cancel_all_except" | "add_guests"
    summary: str
    success: bool
    error_message: str = ""


# --- Response dataclasses ---

@dataclass
class ServiceResponse:
    kind: ResponseKind
    message: str


@dataclass
class SuccessResponse(ServiceResponse):
    event: EventInfo | None = None


@dataclass
class ErrorResponse(ServiceResponse):
    pass


@dataclass
class NoActionResponse(ServiceResponse):
    pass


@dataclass
class ConflictPromptResponse(ServiceResponse):
    options: list[ConflictOption] = field(default_factory=list)
    conflicting_summaries: list[str] = field(default_factory=list)
    pending: PendingEvent | None = None


@dataclass
class BatchCancelPromptResponse(ServiceResponse):
    will_cancel: list[str] = field(default_factory=list)
    will_keep: list[str] = field(default_factory=list)
    pending: PendingBatchCancel | None = None


@dataclass
class QueryResultResponse(ServiceResponse):
    date: str = ""
    events: list[dict] = field(default_factory=list)


@dataclass
class BatchSummaryResponse(ServiceResponse):
    results: list[ActionResult] = field(default_factory=list)


@dataclass
class SlotOption:
    time: str    # "HH:MM"
    label: str   # "09:00" or "09:00 AM"


@dataclass
class SlotSuggestionResponse(ServiceResponse):
    slots: list[SlotOption] = field(default_factory=list)
    pending: PendingEvent | None = None
    is_flexible: bool = True
    all_free_slots: list[str] = field(default_factory=list)


@dataclass
class PendingContactResolution:
    action_type: str                                     # "create"
    parsed_action_json: dict = field(default_factory=dict)  # ParsedEvent.model_dump()
    resolved_contacts: dict = field(default_factory=dict)   # name → email
    unresolved_contacts: list = field(default_factory=list)  # names needing emails
    current_asking: str = ""                              # which name we're asking about


@dataclass
class ContactPromptResponse(ServiceResponse):
    contact_name: str = ""
    pending: PendingContactResolution | None = None


# ---------------------------------------------------------------------------
# ActionService
# ---------------------------------------------------------------------------


class ActionService:
    """Stateless service that orchestrates all business logic.

    Returns structured response objects — never sends messages directly.
    """

    def __init__(self, calendar: CalendarPort, contact_db: ContactDB | None = None) -> None:
        self._calendar = calendar
        self._contact_db = contact_db

    # ------------------------------------------------------------------
    # Public: process free-text
    # ------------------------------------------------------------------

    async def process_text(
        self, text: str, last_event_context: dict | None = None,
    ) -> ServiceResponse:
        """Parse free text via LLM and execute calendar actions.

        Args:
            text: User's free-text message.
            last_event_context: Optional dict with keys event_id, event_summary,
                event_date, event_time — injected into ModifyEvent actions.

        Returns a ServiceResponse subclass describing the outcome.
        """
        from src.core.parser import ModifyEvent, parse_message

        try:
            actions = await parse_message(text)
        except Exception as exc:
            logger.error("Parser error: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Sorry, something went wrong while parsing your message. Please try again.",
            )

        if not actions:
            return NoActionResponse(
                kind=ResponseKind.NO_ACTION,
                message=(
                    "I couldn't find any actionable information in your message. "
                    "Try something like: 'Meeting with Dan tomorrow at 14:00', "
                    "'Cancel my meeting with Dan tomorrow', or "
                    "'Reschedule my meeting with Dan tomorrow to 15:00'."
                ),
            )

        # Inject last event context into ModifyEvent actions
        if last_event_context:
            for i, action in enumerate(actions):
                if isinstance(action, ModifyEvent):
                    actions[i] = action.model_copy(update=last_event_context)

        if len(actions) == 1:
            return await self._execute_single_action(actions[0])

        return await self._execute_batch_actions(actions)

    # ------------------------------------------------------------------
    # Public: conflict resolution
    # ------------------------------------------------------------------

    async def resolve_conflict(
        self,
        pending: PendingEvent,
        choice: str,
        custom_time: str | None = None,
    ) -> ServiceResponse:
        """Resolve a conflict by executing the pending event with chosen time.

        Args:
            pending: The pending event from ConflictPromptResponse.
            choice: "suggested" | "force" | "custom".
            custom_time: Required when choice == "custom". HH:MM format.

        Returns:
            SuccessResponse or ErrorResponse.
        """
        if choice == "custom":
            return await self._resolve_custom_time(pending, custom_time)

        if choice == "suggested":
            time_to_use = pending.time  # suggested time was stored here
        elif choice == "force":
            if pending.pending_type == "create" and pending.parsed_event_json:
                time_to_use = pending.parsed_event_json.get("time")
            else:
                time_to_use = pending.time
        else:
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message=f"Unknown conflict resolution choice: {choice}",
            )

        if not time_to_use:
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Could not determine time to use. Please try again.",
            )

        return await self._execute_pending_event(pending, time_to_use)

    async def _resolve_custom_time(
        self, pending: PendingEvent, custom_time: str | None,
    ) -> ServiceResponse:
        """Validate and execute a custom time choice."""
        if not custom_time or not re.match(r"^\d{1,2}:\d{2}$", custom_time.strip()):
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Invalid time format. Please use HH:MM (e.g. 15:30). Event cancelled.",
            )
        custom_time = custom_time.strip()

        from src.core.conflict_checker import check_conflict

        warning_msg = ""

        if pending.pending_type == "create" and pending.parsed_event_json:
            conflict = await check_conflict(
                self._calendar,
                pending.parsed_event_json["date"],
                custom_time,
                pending.parsed_event_json.get("duration_minutes", 60),
            )
            if conflict.has_conflict:
                clashing = ", ".join(
                    ev.get("summary", "(no title)") for ev in conflict.conflicting_events
                )
                warning_msg = f"\u26a0\ufe0f Note: {custom_time} also conflicts with: {clashing}. Proceeding anyway."
        elif pending.pending_type == "reschedule":
            conflict = await check_conflict(
                self._calendar,
                pending.date,
                custom_time,
                pending.duration or 60,
                exclude_event_id=pending.event_id,
            )
            if conflict.has_conflict:
                clashing = ", ".join(
                    ev.get("summary", "(no title)") for ev in conflict.conflicting_events
                )
                warning_msg = f"\u26a0\ufe0f Note: {custom_time} also conflicts with: {clashing}. Proceeding anyway."

        result = await self._execute_pending_event(pending, custom_time)

        if warning_msg and isinstance(result, SuccessResponse):
            result.message = warning_msg + "\n" + result.message

        return result

    async def _execute_pending_event(
        self, pending: PendingEvent, time_to_use: str,
    ) -> ServiceResponse:
        """Execute a pending event creation or reschedule at the given time."""
        try:
            if pending.pending_type == "create" and pending.parsed_event_json:
                from src.core.parser import ParsedEvent
                pending.parsed_event_json["time"] = time_to_use
                parsed = ParsedEvent(**pending.parsed_event_json)

                # Enrich location (pipeline was bypassed for pending events)
                enriched = await self._enrich_location(parsed)
                if isinstance(enriched, ParsedEvent):
                    parsed = enriched

                created = await self._calendar.add_event(parsed)
                link = created.get("htmlLink", "")
                msg = f"\u2705 Event created: *{parsed.event}* on {parsed.date} at {time_to_use}"
                if parsed.location:
                    msg += f"\n\U0001f4cd {parsed.location}"
                return SuccessResponse(
                    kind=ResponseKind.SUCCESS,
                    message=msg,
                    event=EventInfo(
                        summary=parsed.event, date=parsed.date,
                        time=time_to_use, link=link,
                        location=parsed.location, maps_url=parsed.maps_url,
                        event_id=created.get("id", ""),
                    ),
                )
            elif pending.pending_type == "reschedule":
                updated = await self._calendar.update_event(
                    pending.event_id, pending.date, time_to_use,
                )
                link = updated.get("htmlLink", "")
                summary = updated.get("summary", pending.summary or "Unknown Event")
                return SuccessResponse(
                    kind=ResponseKind.SUCCESS,
                    message=f"\u2705 Event *{summary}* rescheduled to {pending.date} at {time_to_use}",
                    event=EventInfo(
                        summary=summary, date=pending.date,
                        time=time_to_use, link=link,
                        event_id=updated.get("id", pending.event_id or ""),
                    ),
                )
            else:
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="Unknown pending event type.",
                )
        except CalendarError as exc:
            logger.error("Calendar error during conflict resolution: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Something went wrong while saving the event. Please try again.",
            )

    # ------------------------------------------------------------------
    # Public: batch cancel confirmation
    # ------------------------------------------------------------------

    async def confirm_batch_cancel(self, pending: PendingBatchCancel) -> ServiceResponse:
        """Execute a confirmed batch cancel — delete all pending events."""
        succeeded: list[str] = []
        failed: list[str] = []

        for ev in pending.events:
            try:
                await self._calendar.delete_event(ev["id"])
                succeeded.append(ev["summary"])
            except CalendarError as exc:
                logger.error("Failed to cancel '%s': %s", ev["summary"], exc)
                failed.append(ev["summary"])

        lines = []
        for name in succeeded:
            lines.append(f"\u2705 Canceled: *{name}*")
        for name in failed:
            lines.append(f"\u274c Failed to cancel: *{name}*")

        return SuccessResponse(
            kind=ResponseKind.SUCCESS,
            message="\n".join(lines),
        )

    # ------------------------------------------------------------------
    # Public: today's events
    # ------------------------------------------------------------------

    async def get_today_events(self) -> ServiceResponse:
        """Fetch today's calendar events."""
        try:
            events = await self._calendar.get_daily_events()
        except CalendarError as exc:
            logger.error("/today calendar error: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Couldn't fetch today's events. Please try again later.",
            )

        if not events:
            return QueryResultResponse(
                kind=ResponseKind.QUERY_RESULT,
                message="No events scheduled for today.",
                date="today",
                events=[],
            )

        lines = ["*Today's schedule:*\n"]
        for ev in events:
            start = ev.get("start_time", "")
            if "T" in start:
                start = start.split("T")[1][:5]
            end = ev.get("end_time", "")
            if "T" in end:
                end = end.split("T")[1][:5]
            summary = ev.get("summary", "(no title)")
            lines.append(f"\u2022 {start} \u2013 {end}  {summary}")

        return QueryResultResponse(
            kind=ResponseKind.QUERY_RESULT,
            message="\n".join(lines),
            date="today",
            events=events,
        )

    # ------------------------------------------------------------------
    # Public: chore operations
    # ------------------------------------------------------------------

    async def find_chore_slot(
        self,
        name: str,
        frequency_days: int,
        duration_minutes: int,
        preferred_start: str,
        preferred_end: str,
        weeks_ahead: int,
    ) -> dict | None:
        """Find the best recurring time slot for a chore."""
        from src.core.chore_scheduler import find_best_slot

        return await find_best_slot(
            calendar=self._calendar,
            chore_name=name,
            frequency_days=frequency_days,
            duration_minutes=duration_minutes,
            preferred_start=preferred_start,
            preferred_end=preferred_end,
            weeks_ahead=weeks_ahead,
        )

    def create_chore(
        self,
        name: str,
        frequency_days: int,
        assigned_to: str,
        duration_minutes: int = 30,
        preferred_time_start: str = "09:00",
        preferred_time_end: str = "21:00",
    ) -> Chore:
        """Create a chore in the database."""
        from src.data.db import ChoreDB

        db = ChoreDB()
        return db.add_chore(
            name=name,
            frequency_days=frequency_days,
            assigned_to=assigned_to,
            duration_minutes=duration_minutes,
            preferred_time_start=preferred_time_start,
            preferred_time_end=preferred_time_end,
        )

    async def create_chore_calendar_event(
        self,
        chore: Chore,
        slot: dict,
    ) -> ServiceResponse:
        """Create a recurring calendar event for a chore and link it in DB."""
        from src.data.db import ChoreDB

        try:
            created = await self._calendar.add_recurring_event(
                summary=f"\U0001f9f9 {chore.name}",
                description=f"Chore: {chore.name}\nChore ID: {chore.id}",
                start_date=slot["start_date"],
                start_time=slot["start_time"],
                end_time=slot["end_time"],
                frequency_days=slot["frequency_days"],
                occurrences=slot["occurrences"],
            )
            db = ChoreDB()
            db.set_calendar_event_id(chore.id, created["id"])
            return SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message="",  # caller builds the full message
                event=EventInfo(
                    summary=chore.name,
                    date=slot["start_date"],
                    time=slot["start_time"],
                    link=created.get("htmlLink", ""),
                ),
            )
        except CalendarError as exc:
            logger.error("Calendar error creating chore event: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message=str(exc),
            )

    async def delete_chore(self, chore_id: int) -> ServiceResponse:
        """Soft-delete a chore and remove its linked calendar event."""
        from src.data.db import ChoreDB

        try:
            db = ChoreDB()
            chore = db.get_chore(chore_id)
            if chore is None or not chore.active:
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="Chore not found or already deleted.",
                )

            cal_deleted = False
            if chore.calendar_event_id:
                try:
                    await self._calendar.delete_event(chore.calendar_event_id)
                    cal_deleted = True
                except CalendarError as exc:
                    logger.error("Failed to delete calendar event for chore #%d: %s", chore_id, exc)

            db.delete_chore(chore_id)

            msg = f"\u2705 Chore *{chore.name}* deleted."
            if cal_deleted:
                msg += "\nAll linked calendar events have been removed."
            elif chore.calendar_event_id:
                msg += "\n\u26a0\ufe0f Couldn't remove the calendar events \u2014 please delete them manually."

            return SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message=msg,
            )
        except Exception as exc:
            logger.error("delete_chore error: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Something went wrong. Please try again.",
            )

    def list_chores(self, active_only: bool = True) -> list[Chore]:
        """List chores from DB."""
        from src.data.db import ChoreDB

        db = ChoreDB()
        return db.list_all(active_only=active_only)

    def mark_chore_done(self, chore_id: int) -> Chore:
        """Mark a chore as done and advance next_due."""
        from src.data.db import ChoreDB

        db = ChoreDB()
        return db.mark_done(chore_id)

    # ------------------------------------------------------------------
    # Public: contact resolution
    # ------------------------------------------------------------------

    def _resolve_contacts(
        self, mentioned_contacts: list[str], existing_guests: list[str],
    ) -> tuple[dict[str, str], list[str]]:
        """Look up mentioned contact names in the contacts DB.

        Returns (resolved: {name: email}, unresolved: [name, ...]).
        """
        resolved: dict[str, str] = {}
        unresolved: list[str] = []

        if not self._contact_db:
            return resolved, mentioned_contacts

        existing_lower = {e.lower() for e in existing_guests}

        for name in mentioned_contacts:
            contact = self._contact_db.find_by_name(name)
            if contact and contact.email.lower() not in existing_lower:
                resolved[name] = contact.email
            elif contact:
                # Already in guests list
                resolved[name] = contact.email
            else:
                unresolved.append(name)

        return resolved, unresolved

    async def resolve_contact(
        self,
        pending: PendingContactResolution,
        email: str,
    ) -> ServiceResponse:
        """Resolve a pending contact by saving the email and continuing.

        Validates email format, saves to DB, then either asks for the next
        unknown contact or re-executes the original action.
        """
        email = email.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return ContactPromptResponse(
                kind=ResponseKind.CONTACT_PROMPT,
                message=f"'{email}' doesn't look like a valid email. What's the email for {pending.current_asking}?",
                contact_name=pending.current_asking,
                pending=pending,
            )

        # Save contact to DB
        if self._contact_db:
            self._contact_db.add_contact(pending.current_asking, email)

        # Move from unresolved → resolved
        pending.resolved_contacts[pending.current_asking] = email
        pending.unresolved_contacts.remove(pending.current_asking)

        # If more unresolved contacts, ask for the next one
        if pending.unresolved_contacts:
            next_name = pending.unresolved_contacts[0]
            pending.current_asking = next_name
            return ContactPromptResponse(
                kind=ResponseKind.CONTACT_PROMPT,
                message=f"I don't have an email for *{next_name}*. What's their email?",
                contact_name=next_name,
                pending=pending,
            )

        # All resolved — rebuild the parsed action with guests and re-execute
        all_emails = list(pending.resolved_contacts.values())

        if pending.action_type == "modify":
            from src.core.parser import ModifyEvent
            existing_guests = pending.parsed_action_json.get("add_guests", [])
            merged = list({e.lower(): e for e in existing_guests + all_emails}.values())
            pending.parsed_action_json["add_guests"] = merged
            pending.parsed_action_json["mentioned_contacts"] = []
            parsed = ModifyEvent(**pending.parsed_action_json)
            return await self._execute_single_action(parsed)

        existing_guests = pending.parsed_action_json.get("guests", [])
        merged = list({e.lower(): e for e in existing_guests + all_emails}.values())
        pending.parsed_action_json["guests"] = merged
        pending.parsed_action_json["mentioned_contacts"] = []

        from src.core.parser import ParsedEvent
        parsed = ParsedEvent(**pending.parsed_action_json)
        return await self._execute_single_action(parsed)

    # ------------------------------------------------------------------
    # Enricher pipeline for create events
    # ------------------------------------------------------------------

    _CREATE_PIPELINE = ["_enrich_contacts", "_enrich_time", "_enrich_location", "_enrich_conflicts"]

    async def _enrich_contacts(self, parsed: object) -> object | ServiceResponse:
        """Resolve mentioned contacts into guest emails.

        Returns:
            ParsedEvent with guests populated, or ContactPromptResponse if unknown contacts.
        """
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent) or not parsed.mentioned_contacts:
            return parsed

        resolved, unresolved = self._resolve_contacts(
            parsed.mentioned_contacts, parsed.guests,
        )
        if unresolved:
            first_unknown = unresolved[0]
            pending = PendingContactResolution(
                action_type="create",
                parsed_action_json=parsed.model_dump(),
                resolved_contacts=resolved,
                unresolved_contacts=unresolved,
                current_asking=first_unknown,
            )
            return ContactPromptResponse(
                kind=ResponseKind.CONTACT_PROMPT,
                message=f"I don't have an email for *{first_unknown}*. What's their email?",
                contact_name=first_unknown,
                pending=pending,
            )
        # All contacts resolved — merge emails into guests
        all_emails = list(resolved.values())
        existing = parsed.guests
        merged = list({e.lower(): e for e in existing + all_emails}.values())
        return parsed.model_copy(update={"guests": merged, "mentioned_contacts": []})

    async def _enrich_time(self, parsed: object) -> object | ServiceResponse:
        """Suggest time slots if no time is specified.

        Returns:
            ParsedEvent unchanged if time is present, or SlotSuggestionResponse.
        """
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent) or parsed.time:
            return parsed

        return await self._suggest_slots(parsed)

    async def _enrich_location(self, parsed: object) -> object | ServiceResponse:
        """Enrich raw location text via Google Maps Places API.

        Returns:
            ParsedEvent with enriched location + maps_url, or unchanged if no location.
        """
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent) or not parsed.location:
            return parsed

        from src.config import settings

        if not settings.GOOGLE_MAPS_API_KEY:
            return parsed

        from src.integrations.google_maps import enrich_location

        result = await enrich_location(parsed.location, settings.GOOGLE_MAPS_API_KEY)
        if result is None:
            return parsed

        if result.formatted_address:
            display = f"{result.display_name}, {result.formatted_address}"
        else:
            display = result.display_name
        return parsed.model_copy(update={"location": display, "maps_url": result.maps_url})

    async def _enrich_conflicts(self, parsed: object) -> object | ServiceResponse:
        """Check for time conflicts before creating.

        Returns:
            ParsedEvent unchanged if no conflict, or ConflictPromptResponse.
        """
        from src.core.conflict_checker import check_conflict
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent):
            return parsed

        conflict = await check_conflict(
            self._calendar, parsed.date, parsed.time, parsed.duration_minutes,
        )
        if conflict.has_conflict:
            return self._build_conflict_response(
                conflict, parsed.time,
                PendingEvent(
                    pending_type="create",
                    parsed_event_json=parsed.model_dump(),
                ),
            )
        return parsed

    async def _create_event(self, parsed: object) -> ServiceResponse:
        """Create the calendar event after all enrichers have passed."""
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent):
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Internal error: expected ParsedEvent for event creation.",
            )

        try:
            created = await self._calendar.add_event(parsed)
            link = created.get("htmlLink", "")
            msg = f"\u2705 Event created: *{parsed.event}* on {parsed.date} at {parsed.time}"
            if parsed.location:
                msg += f"\n\U0001f4cd {parsed.location}"
            return SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message=msg,
                event=EventInfo(
                    summary=parsed.event, date=parsed.date,
                    time=parsed.time, link=link,
                    location=parsed.location, maps_url=parsed.maps_url,
                    event_id=created.get("id", ""),
                ),
            )
        except CalendarError as exc:
            logger.error("Calendar write error: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="I parsed your event but couldn't save it to Google Calendar. Please try again later.",
            )

    async def _run_create_pipeline(self, parsed: object) -> ServiceResponse:
        """Run the create enricher pipeline, stopping at the first pause."""
        for step_name in self._CREATE_PIPELINE:
            result = await getattr(self, step_name)(parsed)
            if isinstance(result, ServiceResponse):
                return result     # Paused — need user input
            parsed = result       # Enriched — continue
        return await self._create_event(parsed)

    async def _run_create_pipeline_batch(self, parsed: object) -> ActionResult:
        """Run the create pipeline in batch mode, converting pauses to errors."""
        from src.core.parser import ParsedEvent

        for step_name in self._CREATE_PIPELINE:
            result = await getattr(self, step_name)(parsed)
            if isinstance(result, ServiceResponse):
                return ActionResult(
                    action_type="create",
                    summary=parsed.event if isinstance(parsed, ParsedEvent) else "Unknown",
                    success=False,
                    error_message=self._batch_error_from_response(result),
                )
            parsed = result

        if not isinstance(parsed, ParsedEvent):
            return ActionResult(
                action_type="create", summary="Unknown",
                success=False, error_message="Internal error",
            )

        try:
            await self._calendar.add_event(parsed)
            return ActionResult(
                action_type="create", summary=parsed.event, success=True,
            )
        except CalendarError as exc:
            return ActionResult(
                action_type="create", summary=parsed.event,
                success=False, error_message=str(exc),
            )

    @staticmethod
    def _batch_error_from_response(response: ServiceResponse) -> str:
        """Convert a pipeline pause response into a batch error message."""
        if isinstance(response, ContactPromptResponse):
            pending = response.pending
            if pending:
                return f"Unknown contact: {', '.join(pending.unresolved_contacts)}"
            return f"Unknown contact: {response.contact_name}"
        if isinstance(response, SlotSuggestionResponse):
            return "No time specified — send as a single message for slot suggestions"
        if isinstance(response, ConflictPromptResponse):
            return f"Conflict with: {', '.join(response.conflicting_summaries)}"
        return response.message

    # ------------------------------------------------------------------
    # Internal: modify last event
    # ------------------------------------------------------------------

    async def _execute_modify(self, parsed: object) -> ServiceResponse:
        """Execute a modify-last-event action."""
        from src.core.parser import ModifyEvent, ParsedEvent

        if not isinstance(parsed, ModifyEvent):
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Internal error: expected ModifyEvent.",
            )

        if not parsed.event_id:
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="No recent event to modify. Create or reschedule an event first.",
            )

        fields: dict = {}
        maps_url = ""

        # Location enrichment (reuse _enrich_location via a temp ParsedEvent)
        if parsed.add_location:
            temp = ParsedEvent(
                event="temp", date=parsed.event_date or "2000-01-01",
                time=parsed.event_time or "12:00",
                location=parsed.add_location,
            )
            enriched = await self._enrich_location(temp)
            if isinstance(enriched, ParsedEvent):
                fields["location"] = enriched.location
                maps_url = enriched.maps_url
            else:
                fields["location"] = parsed.add_location

        # Contact resolution
        if parsed.mentioned_contacts:
            resolved, unresolved = self._resolve_contacts(
                parsed.mentioned_contacts, parsed.add_guests,
            )
            if unresolved:
                first_unknown = unresolved[0]
                pending = PendingContactResolution(
                    action_type="modify",
                    parsed_action_json=parsed.model_dump(),
                    resolved_contacts=resolved,
                    unresolved_contacts=unresolved,
                    current_asking=first_unknown,
                )
                return ContactPromptResponse(
                    kind=ResponseKind.CONTACT_PROMPT,
                    message=f"I don't have an email for *{first_unknown}*. What's their email?",
                    contact_name=first_unknown,
                    pending=pending,
                )
            fields.setdefault("add_guests", []).extend(resolved.values())

        if parsed.add_guests:
            fields.setdefault("add_guests", []).extend(parsed.add_guests)
        if parsed.remove_guests:
            fields["remove_guests"] = parsed.remove_guests
        if parsed.new_time:
            fields["time"] = parsed.new_time
        if parsed.new_description:
            fields["description"] = parsed.new_description

        if not fields:
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="I couldn't determine what to modify. Please be more specific.",
            )

        try:
            updated = await self._calendar.update_event_fields(parsed.event_id, **fields)

            parts: list[str] = []
            if "location" in fields:
                parts.append(f"location \u2192 {fields['location']}")
            if "add_guests" in fields:
                parts.append(f"added {', '.join(fields['add_guests'])}")
            if "remove_guests" in fields:
                parts.append(f"removed {', '.join(fields['remove_guests'])}")
            if "time" in fields:
                parts.append(f"time \u2192 {fields['time']}")
            if "description" in fields:
                parts.append("description updated")

            msg = f"\u270f\ufe0f Updated *{parsed.event_summary}*: {', '.join(parts)}"
            return SuccessResponse(
                kind=ResponseKind.SUCCESS,
                message=msg,
                event=EventInfo(
                    event_id=updated.get("id", parsed.event_id),
                    summary=parsed.event_summary,
                    date=parsed.event_date,
                    time=fields.get("time", parsed.event_time),
                    link=updated.get("htmlLink", ""),
                    location=fields.get("location", ""),
                    maps_url=maps_url,
                ),
            )
        except CalendarError as exc:
            logger.error("Calendar update_event_fields error: %s", exc)
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Couldn't update the event. Please try again.",
            )

    # ------------------------------------------------------------------
    # Internal: single-action execution
    # ------------------------------------------------------------------

    async def _execute_single_action(self, parsed: object) -> ServiceResponse:
        """Execute a single parsed action with full interactive flow."""
        from src.core.conflict_checker import check_conflict, extract_event_duration_minutes
        from src.core.parser import (
            AddGuests, CancelAllExcept, CancelEvent, ModifyEvent, ParsedEvent,
            QueryEvents, RescheduleEvent, batch_exclude_events, match_event,
        )

        if isinstance(parsed, ModifyEvent):
            return await self._execute_modify(parsed)

        elif isinstance(parsed, ParsedEvent):
            return await self._run_create_pipeline(parsed)

        elif isinstance(parsed, CancelEvent):
            try:
                all_events = await self._calendar.find_events(target_date=parsed.date)
                if not all_events:
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=f"There are no events on {parsed.date} to cancel.",
                    )

                matched = await match_event(parsed.event_summary, all_events)
                if matched is None:
                    summaries = ", ".join(ev["summary"] for ev in all_events)
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=(
                            f"I couldn't match '{parsed.event_summary}' to any event on {parsed.date}.\n"
                            f"Events that day: {summaries}"
                        ),
                    )

                await self._calendar.delete_event(matched["id"])
                return SuccessResponse(
                    kind=ResponseKind.SUCCESS,
                    message=f"\u2705 Event canceled: *{matched['summary']}*",
                )
            except CalendarError as exc:
                logger.error("Calendar delete error: %s", exc)
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="I found the event but couldn't cancel it. Please try again later.",
                )

        elif isinstance(parsed, RescheduleEvent):
            try:
                all_events = await self._calendar.find_events(target_date=parsed.original_date)
                if not all_events:
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=f"There are no events on {parsed.original_date} to reschedule.",
                    )

                matched = await match_event(parsed.event_summary, all_events)
                if matched is None:
                    summaries = ", ".join(ev["summary"] for ev in all_events)
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=(
                            f"I couldn't match '{parsed.event_summary}' to any event on {parsed.original_date}.\n"
                            f"Events that day: {summaries}"
                        ),
                    )

                duration = extract_event_duration_minutes(matched)
                conflict = await check_conflict(
                    self._calendar, parsed.original_date, parsed.new_time,
                    duration, exclude_event_id=matched["id"],
                )
                if conflict.has_conflict:
                    return self._build_conflict_response(
                        conflict, parsed.new_time,
                        PendingEvent(
                            pending_type="reschedule",
                            event_id=matched["id"],
                            date=parsed.original_date,
                            time=parsed.new_time,
                            duration=duration,
                            summary=matched.get("summary", "Unknown Event"),
                        ),
                    )

                updated = await self._calendar.update_event(
                    matched["id"], parsed.original_date, parsed.new_time,
                )
                link = updated.get("htmlLink", "")
                summary = updated.get("summary", "Unknown Event")
                return SuccessResponse(
                    kind=ResponseKind.SUCCESS,
                    message=(
                        f"\u2705 Event *{summary}* "
                        f"rescheduled to {parsed.original_date} at {parsed.new_time}"
                    ),
                    event=EventInfo(
                        summary=summary, date=parsed.original_date,
                        time=parsed.new_time, link=link,
                        event_id=updated.get("id", matched.get("id", "")),
                    ),
                )
            except CalendarError as exc:
                logger.error("Calendar reschedule error: %s", exc)
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="I found the event but couldn't reschedule it. Please try again later.",
                )

        elif isinstance(parsed, QueryEvents):
            try:
                events = await self._calendar.find_events(target_date=parsed.date)
                if not events:
                    return QueryResultResponse(
                        kind=ResponseKind.QUERY_RESULT,
                        message=f"No events scheduled for {parsed.date}.",
                        date=parsed.date,
                        events=[],
                    )

                lines = [f"*Events on {parsed.date}:*\n"]
                for ev in events:
                    start = ev.get("start_time", "")
                    if "T" in start:
                        start = start.split("T")[1][:5]
                    end = ev.get("end_time", "")
                    if "T" in end:
                        end = end.split("T")[1][:5]
                    summary = ev.get("summary", "(no title)")
                    lines.append(f"\u2022 {start} \u2013 {end}  {summary}")

                return QueryResultResponse(
                    kind=ResponseKind.QUERY_RESULT,
                    message="\n".join(lines),
                    date=parsed.date,
                    events=events,
                )
            except CalendarError as exc:
                logger.error("Calendar query error: %s", exc)
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="Couldn't fetch events. Please try again later.",
                )

        elif isinstance(parsed, CancelAllExcept):
            try:
                all_events = await self._calendar.find_events(target_date=parsed.date)
                if not all_events:
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=f"There are no events on {parsed.date} to cancel.",
                    )

                to_cancel = await batch_exclude_events(parsed.exceptions, all_events)
                if not to_cancel:
                    return SuccessResponse(
                        kind=ResponseKind.SUCCESS,
                        message=f"All events on {parsed.date} match your exceptions \u2014 nothing to cancel.",
                    )

                keep_names = [
                    ev.get("summary", "(no title)") for ev in all_events if ev not in to_cancel
                ]
                cancel_names = [ev.get("summary", "(no title)") for ev in to_cancel]

                pending = PendingBatchCancel(
                    events=[
                        {"id": ev["id"], "summary": ev.get("summary", "(no title)")}
                        for ev in to_cancel
                    ],
                )

                msg = "*Cancel all except \u2014 please confirm:*\n\n"
                msg += "*Will cancel:*\n"
                for name in cancel_names:
                    msg += f"  \u2022 {name}\n"
                if keep_names:
                    msg += "\n*Will keep:*\n"
                    for name in keep_names:
                        msg += f"  \u2022 {name}\n"

                return BatchCancelPromptResponse(
                    kind=ResponseKind.BATCH_CANCEL_PROMPT,
                    message=msg,
                    will_cancel=cancel_names,
                    will_keep=keep_names,
                    pending=pending,
                )
            except CalendarError as exc:
                logger.error("Calendar error during cancel-all-except: %s", exc)
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="Something went wrong while processing your request. Please try again later.",
                )

        elif isinstance(parsed, AddGuests):
            try:
                all_events = await self._calendar.find_events(target_date=parsed.date)
                if not all_events:
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=f"There are no events on {parsed.date} to add guests to.",
                    )

                matched = await match_event(parsed.event_summary, all_events)
                if matched is None:
                    summaries = ", ".join(ev["summary"] for ev in all_events)
                    return ErrorResponse(
                        kind=ResponseKind.ERROR,
                        message=(
                            f"I couldn't match '{parsed.event_summary}' to any event on {parsed.date}.\n"
                            f"Events that day: {summaries}"
                        ),
                    )

                updated = await self._calendar.add_guests(matched["id"], parsed.guests)
                guests_str = ", ".join(parsed.guests)
                return SuccessResponse(
                    kind=ResponseKind.SUCCESS,
                    message=f"\u2705 Added {guests_str} to *{matched['summary']}*",
                )
            except CalendarError as exc:
                logger.error("Calendar add_guests error: %s", exc)
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message="I found the event but couldn't add guests. Please try again later.",
                )

        return ErrorResponse(
            kind=ResponseKind.ERROR,
            message="Unknown action type.",
        )

    # ------------------------------------------------------------------
    # Internal: batch execution
    # ------------------------------------------------------------------

    async def _execute_batch_actions(self, actions: list) -> BatchSummaryResponse:
        """Process multiple actions sequentially, collecting results for a summary."""
        from src.core.conflict_checker import check_conflict, extract_event_duration_minutes
        from src.core.parser import (
            AddGuests, CancelAllExcept, CancelEvent, ParsedEvent, QueryEvents,
            RescheduleEvent, batch_exclude_events, batch_match_events, match_event,
        )

        results: list[ActionResult] = []

        # Group cancels by date to optimize find_events calls
        cancel_actions: list[tuple[int, CancelEvent]] = []
        other_actions: list[tuple[int, object]] = []
        for i, action in enumerate(actions):
            if isinstance(action, CancelEvent):
                cancel_actions.append((i, action))
            else:
                other_actions.append((i, action))

        # Process grouped cancels: one find_events + one batch_match per date
        cancel_results_map: dict[int, ActionResult] = {}
        if cancel_actions:
            cancels_by_date: dict[str, list[tuple[int, CancelEvent]]] = {}
            for idx, action in cancel_actions:
                cancels_by_date.setdefault(action.date, []).append((idx, action))

            for date_str, date_cancels in cancels_by_date.items():
                try:
                    all_events = await self._calendar.find_events(target_date=date_str)
                    if not all_events:
                        for idx, action in date_cancels:
                            cancel_results_map[idx] = ActionResult(
                                action_type="cancel",
                                summary=action.event_summary,
                                success=False,
                                error_message=f"No events on {date_str}",
                            )
                        continue

                    descriptions = [action.event_summary for _, action in date_cancels]
                    matched = await batch_match_events(descriptions, all_events)

                    for (idx, action), matched_ev in zip(date_cancels, matched):
                        if matched_ev is None:
                            cancel_results_map[idx] = ActionResult(
                                action_type="cancel",
                                summary=action.event_summary,
                                success=False,
                                error_message="Could not find matching event",
                            )
                        else:
                            try:
                                await self._calendar.delete_event(matched_ev["id"])
                                cancel_results_map[idx] = ActionResult(
                                    action_type="cancel",
                                    summary=matched_ev.get("summary", action.event_summary),
                                    success=True,
                                )
                            except CalendarError as exc:
                                cancel_results_map[idx] = ActionResult(
                                    action_type="cancel",
                                    summary=matched_ev.get("summary", action.event_summary),
                                    success=False,
                                    error_message=str(exc),
                                )
                except CalendarError as exc:
                    for idx, action in date_cancels:
                        cancel_results_map[idx] = ActionResult(
                            action_type="cancel",
                            summary=action.event_summary,
                            success=False,
                            error_message=str(exc),
                        )

        # Process other actions sequentially
        other_results_map: dict[int, ActionResult] = {}
        for idx, action in other_actions:
            if isinstance(action, ParsedEvent):
                other_results_map[idx] = await self._run_create_pipeline_batch(action)
                continue
            elif isinstance(action, RescheduleEvent):
                try:
                    all_events = await self._calendar.find_events(target_date=action.original_date)
                    matched_ev = await match_event(action.event_summary, all_events) if all_events else None
                    if matched_ev is None:
                        other_results_map[idx] = ActionResult(
                            action_type="reschedule", summary=action.event_summary,
                            success=False, error_message="Could not find matching event",
                        )
                        continue

                    duration = extract_event_duration_minutes(matched_ev)
                    conflict = await check_conflict(
                        self._calendar, action.original_date, action.new_time,
                        duration, exclude_event_id=matched_ev["id"],
                    )
                    if conflict.has_conflict:
                        clashing = ", ".join(
                            ev.get("summary", "(no title)") for ev in conflict.conflicting_events
                        )
                        other_results_map[idx] = ActionResult(
                            action_type="reschedule", summary=action.event_summary,
                            success=False, error_message=f"Conflict with: {clashing}",
                        )
                        continue

                    await self._calendar.update_event(matched_ev["id"], action.original_date, action.new_time)
                    other_results_map[idx] = ActionResult(
                        action_type="reschedule", summary=action.event_summary, success=True,
                    )
                except CalendarError as exc:
                    other_results_map[idx] = ActionResult(
                        action_type="reschedule", summary=action.event_summary,
                        success=False, error_message=str(exc),
                    )
            elif isinstance(action, QueryEvents):
                try:
                    events = await self._calendar.find_events(target_date=action.date)
                    if not events:
                        other_results_map[idx] = ActionResult(
                            action_type="query", summary=f"No events on {action.date}",
                            success=True,
                        )
                    else:
                        lines = []
                        for ev in events:
                            start = ev.get("start_time", "")
                            if "T" in start:
                                start = start.split("T")[1][:5]
                            summary = ev.get("summary", "(no title)")
                            lines.append(f"{start} {summary}")
                        other_results_map[idx] = ActionResult(
                            action_type="query", summary="; ".join(lines), success=True,
                        )
                except CalendarError as exc:
                    other_results_map[idx] = ActionResult(
                        action_type="query", summary=f"Query {action.date}",
                        success=False, error_message=str(exc),
                    )
            elif isinstance(action, CancelAllExcept):
                try:
                    all_events = await self._calendar.find_events(target_date=action.date)
                    if not all_events:
                        other_results_map[idx] = ActionResult(
                            action_type="cancel_all_except",
                            summary=f"No events on {action.date}",
                            success=False, error_message=f"No events on {action.date}",
                        )
                        continue

                    to_cancel = await batch_exclude_events(action.exceptions, all_events)
                    if not to_cancel:
                        other_results_map[idx] = ActionResult(
                            action_type="cancel_all_except",
                            summary="Nothing to cancel \u2014 all events match exceptions",
                            success=True,
                        )
                        continue

                    canceled_names = []
                    for ev in to_cancel:
                        try:
                            await self._calendar.delete_event(ev["id"])
                            canceled_names.append(ev.get("summary", "(no title)"))
                        except CalendarError:
                            pass

                    other_results_map[idx] = ActionResult(
                        action_type="cancel_all_except",
                        summary=f"Canceled {len(canceled_names)} events",
                        success=True,
                    )
                except CalendarError as exc:
                    other_results_map[idx] = ActionResult(
                        action_type="cancel_all_except",
                        summary="Cancel all except",
                        success=False, error_message=str(exc),
                    )
            elif isinstance(action, AddGuests):
                try:
                    all_events = await self._calendar.find_events(target_date=action.date)
                    matched_ev = await match_event(action.event_summary, all_events) if all_events else None
                    if matched_ev is None:
                        other_results_map[idx] = ActionResult(
                            action_type="add_guests",
                            summary=action.event_summary,
                            success=False, error_message="Could not find matching event",
                        )
                        continue

                    await self._calendar.add_guests(matched_ev["id"], action.guests)
                    guests_str = ", ".join(action.guests)
                    other_results_map[idx] = ActionResult(
                        action_type="add_guests",
                        summary=f"Added {guests_str} to {matched_ev.get('summary', action.event_summary)}",
                        success=True,
                    )
                except CalendarError as exc:
                    other_results_map[idx] = ActionResult(
                        action_type="add_guests",
                        summary=action.event_summary,
                        success=False, error_message=str(exc),
                    )

        # Reassemble results in original order
        all_results_map = {}
        all_results_map.update(cancel_results_map)
        all_results_map.update(other_results_map)

        ordered_results = [all_results_map[i] for i in sorted(all_results_map.keys())]

        lines = [f"*Processed {len(ordered_results)} actions:*\n"]
        for r in ordered_results:
            if r.success:
                lines.append(f'\u2705 {r.action_type.replace("_", " ").title()}: "{r.summary}"')
            else:
                lines.append(
                    f'\u274c {r.action_type.replace("_", " ").title()}: "{r.summary}" \u2014 {r.error_message}'
                )

        return BatchSummaryResponse(
            kind=ResponseKind.BATCH_SUMMARY,
            message="\n".join(lines),
            results=ordered_results,
        )

    # ------------------------------------------------------------------
    # Internal: build conflict response
    # ------------------------------------------------------------------

    def _build_conflict_response(
        self,
        conflict: object,
        original_time: str,
        pending: PendingEvent,
    ) -> ConflictPromptResponse:
        """Build a ConflictPromptResponse from a ConflictResult."""
        clashing = [
            ev.get("summary", "(no title)") for ev in conflict.conflicting_events
        ]

        options: list[ConflictOption] = []
        if conflict.suggested_time:
            options.append(ConflictOption(
                key="suggested",
                label=f"Use {conflict.suggested_time}",
                time=conflict.suggested_time,
            ))
            # Store suggested time in pending for resolve_conflict
            pending.time = conflict.suggested_time
        options.append(ConflictOption(
            key="force",
            label=f"Force {original_time}",
            time=original_time,
        ))
        options.append(ConflictOption(
            key="custom",
            label="Enter custom time",
        ))
        options.append(ConflictOption(
            key="cancel",
            label="Cancel",
        ))

        clashing_str = ", ".join(clashing)
        msg = (
            f"\u26a0\ufe0f *Time conflict detected!*\n"
            f"Your requested time ({original_time}) overlaps with: {clashing_str}"
        )

        return ConflictPromptResponse(
            kind=ResponseKind.CONFLICT_PROMPT,
            message=msg,
            options=options,
            conflicting_summaries=clashing,
            pending=pending,
        )

    # ------------------------------------------------------------------
    # Public: slot selection
    # ------------------------------------------------------------------

    async def select_slot(self, pending: PendingEvent, selected_time: str) -> ServiceResponse:
        """Create an event at the user-selected time slot.

        Re-checks for conflicts before creating (handles stale data and
        user-typed times that weren't in the suggested list).
        """
        from src.core.conflict_checker import check_conflict

        if pending.parsed_event_json:
            conflict = await check_conflict(
                self._calendar,
                pending.parsed_event_json["date"],
                selected_time,
                pending.parsed_event_json.get("duration_minutes", 60),
            )
            if conflict.has_conflict:
                clashing = ", ".join(
                    ev.get("summary", "(no title)") for ev in conflict.conflicting_events
                )
                return ErrorResponse(
                    kind=ResponseKind.ERROR,
                    message=f"Sorry, {selected_time} conflicts with: {clashing}. Please pick another time.",
                )

        return await self._execute_pending_event(pending, selected_time)

    # ------------------------------------------------------------------
    # Internal: suggest slots for missing time
    # ------------------------------------------------------------------

    async def _suggest_slots(self, parsed: object) -> ServiceResponse:
        """Suggest free time slots when a create request has no time specified."""
        from src.core.conflict_checker import get_free_slots
        from src.core.parser import ParsedEvent

        if not isinstance(parsed, ParsedEvent):
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message="Internal error: expected ParsedEvent for slot suggestion.",
            )

        result = await get_free_slots(
            self._calendar, parsed.date, parsed.duration_minutes,
        )

        if not result.suggested:
            return ErrorResponse(
                kind=ResponseKind.ERROR,
                message=f"No available slots on {parsed.date} for a {parsed.duration_minutes}-minute event.",
            )

        slot_options = [SlotOption(time=t, label=t) for t in result.suggested]

        with_guests = ""
        if parsed.guests:
            with_guests = f" with {', '.join(parsed.guests)}"

        return SlotSuggestionResponse(
            kind=ResponseKind.SLOT_SUGGESTION,
            message=(
                f"I found some available times for *{parsed.event}*{with_guests} on {parsed.date}. "
                f"Pick one, or type any time that works for you:"
            ),
            slots=slot_options,
            pending=PendingEvent(
                pending_type="create",
                parsed_event_json=parsed.model_dump(),
            ),
            is_flexible=True,
            all_free_slots=result.all_available,
        )
