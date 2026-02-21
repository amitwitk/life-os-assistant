# LifeOS Assistant — Architecture Documentation

## Table of Contents

- [High-Level Architecture](#high-level-architecture)
- [Directory Structure](#directory-structure)
- [Action Framework](#action-framework)
- [Integrations](#integrations)
- [Decoupling the UI](#decoupling-the-ui)
- [LLM-Agnostic Logic](#llm-agnostic-logic)
- [Data Layer](#data-layer)
- [Configuration](#configuration)
- [Testing](#testing)
- [CI/CD](#cicd)

---

## High-Level Architecture

LifeOS Assistant follows **Hexagonal Architecture** (Ports & Adapters). The core business logic never imports concrete implementations — it depends only on abstract interfaces (Python `Protocol` classes). External services (calendars, notifications, LLMs) are plugged in via adapters.

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Bot (UI)                     │
│              src/bot/telegram_bot.py                     │
│     Thin rendering layer — maps ServiceResponse to      │
│     messages, keyboards, and callback handlers          │
└──────────────────────┬──────────────────────────────────┘
                       │ ServiceResponse
┌──────────────────────▼──────────────────────────────────┐
│                   ActionService (Core)                   │
│             src/core/action_service.py                   │
│     Orchestrates parsing, enrichment, and execution     │
│     UI-agnostic — returns typed response objects        │
├─────────────┬──────────────┬────────────────────────────┤
│   Parser    │  Enrichers   │  Conflict Checker          │
│  parser.py  │  (pipeline)  │  conflict_checker.py       │
└──────┬──────┴──────┬───────┴─────────┬──────────────────┘
       │             │                 │
┌──────▼──────┐ ┌────▼─────┐ ┌────────▼────────┐
│  LLM Port   │ │ Maps API │ │ CalendarPort    │
│  llm.py     │ │ google   │ │ (Protocol)      │
│             │ │ _maps.py │ │                 │
└──────┬──────┘ └──────────┘ └────────┬────────┘
       │                              │
  ┌────▼────┐              ┌──────────▼──────────┐
  │ Gemini  │              │   Google Calendar   │
  │Anthropic│              │   Outlook / 365     │
  │ OpenAI  │              │   CalDAV (iCloud,   │
  │ Cohere  │              │   Nextcloud, etc.)  │
  └─────────┘              └─────────────────────┘
```

### Key Principles

1. **Core never imports adapters.** `action_service.py` receives a `CalendarPort` via constructor injection — it never knows which calendar provider is running.
2. **UI is a rendering layer.** The Telegram bot maps `ServiceResponse` objects to messages and keyboards. Swapping to a different UI (web, CLI, WhatsApp) requires only a new renderer.
3. **LLM is swappable.** A single `complete()` function routes to the configured provider. Adding a new LLM is one async function.
4. **Enrichment is a pipeline.** Each enricher step either transforms the parsed event or pauses execution to ask the user a question (contacts, time slots, conflicts).

---

## Directory Structure

```
src/
├── config.py                  # Pydantic Settings singleton, env var validation
├── ports/                     # Abstract interfaces (Python Protocol)
│   ├── calendar_port.py       # CalendarPort + CalendarError exception
│   └── notification_port.py   # NotificationPort
├── adapters/                  # Concrete implementations of ports
│   ├── calendar_factory.py    # Factory: CALENDAR_PROVIDER → adapter instance
│   ├── google_calendar.py     # Google Calendar API v3
│   ├── outlook_calendar.py    # Microsoft Graph (Outlook/365)
│   ├── caldav_calendar.py     # CalDAV (iCloud, Nextcloud, Fastmail)
│   └── telegram_notifier.py   # Telegram Bot notification sender
├── core/                      # Business logic — NEVER imports adapters
│   ├── parser.py              # NL → structured intents (Pydantic models)
│   ├── action_service.py      # Orchestrator: parse → enrich → execute
│   ├── conflict_checker.py    # Time conflict detection + free slot finder
│   ├── chore_scheduler.py     # Smart recurring chore slot optimizer
│   ├── scheduler.py           # Scheduling utilities
│   ├── llm.py                 # LLM provider strategy (4 providers)
│   └── transcriber.py         # Audio → text via OpenAI Whisper
├── data/                      # Persistence layer
│   ├── models.py              # Dataclasses: Chore, Contact
│   └── db.py                  # SQLite: ChoreDB, ContactDB
├── integrations/              # External service wrappers
│   ├── google_auth.py         # Google OAuth2 flow
│   ├── google_maps.py         # Google Maps Places API
│   ├── ms_auth.py             # Microsoft Graph auth
│   └── gcal_service.py        # Deprecated facade (backward compat)
└── bot/                       # UI adapter
    └── telegram_bot.py        # Handlers, renderers, conversation flows
```

---

## Action Framework

The action framework is the core pipeline that turns natural language into calendar operations. It has four stages: **Parse**, **Enrich**, **Execute**, **Respond**.

### 1. Parse — Natural Language to Structured Intents

`src/core/parser.py` uses a **schema-driven prompt** that is auto-generated from Pydantic models. Each intent is a model class registered in the intent registry:

| Intent | Model | Description |
|--------|-------|-------------|
| `create` | `ParsedEvent` | Create a calendar event |
| `cancel` | `CancelEvent` | Cancel an existing event |
| `reschedule` | `RescheduleEvent` | Move event to a new time |
| `query` | `QueryEvents` | View events on a date |
| `cancel_all_except` | `CancelAllExcept` | Delete all events except listed |
| `add_guests` | `AddGuests` | Add invitees to an event |
| `modify` | `ModifyEvent` | Modify the last created/rescheduled event |

The system prompt is built dynamically from four dictionaries:

- **`INTENT_REGISTRY`** — maps intent string to model class
- **`_INTENT_LABELS`** — human-readable label for each intent
- **`_INTENT_TRIGGERS`** — when to use each intent (context for the LLM)
- **`_BEHAVIORAL_RULES`** — constraints per intent (e.g., "only use modify when referring to a recent event")

Adding a new intent requires: (1) a Pydantic model, (2) entries in all four dictionaries. The prompt rebuilds automatically.

### 2. Enrich — Pipeline of Transformations

When a `create` intent is detected, the parsed event passes through a sequential enricher pipeline (`_CREATE_PIPELINE`):

```
_enrich_contacts → _enrich_time → _enrich_location → _enrich_conflicts
```

Each enricher either:
- **Transforms** the event (e.g., resolves "Dan" → "dan@example.com") and passes it forward
- **Pauses** execution by returning a `ServiceResponse` that asks the user a question (e.g., "What's Dan's email?", "Pick a time slot", "There's a conflict — what do you want to do?")

When the user replies, the bot resumes execution from the paused state using pending objects stored in `context.user_data`.

### 3. Execute — Calendar Operations

After enrichment, `_execute_single_action` dispatches to the appropriate handler based on the parsed intent type:

- `ModifyEvent` → `_execute_modify()`
- `ParsedEvent` → `_create_event()`
- `CancelEvent` → match event by name, then `calendar.delete_event()`
- `RescheduleEvent` → match event, then `calendar.update_event()`
- `QueryEvents` → `calendar.find_events()` or `calendar.get_daily_events()`
- `CancelAllExcept` → batch cancel with confirmation
- `AddGuests` → `calendar.add_guests()`

Batch operations (multiple intents in one message) are optimized: cancels are grouped by date to minimize API calls.

### 4. Respond — Typed Response Objects

Every operation returns a `ServiceResponse` subclass. The bot layer pattern-matches on the type to render the appropriate UI:

| Response Type | UI Rendering |
|---------------|-------------|
| `SuccessResponse` | Text message + calendar link + maps link |
| `ErrorResponse` | Error text |
| `ConflictPromptResponse` | Inline keyboard: Suggested / Force / Custom / Cancel |
| `SlotSuggestionResponse` | Inline keyboard: time slot buttons |
| `ContactPromptResponse` | Text prompt awaiting email input |
| `BatchCancelPromptResponse` | Confirm / Abort buttons |
| `QueryResultResponse` | Formatted event list |
| `BatchSummaryResponse` | Summary of batch operations |
| `NoActionResponse` | "No actionable info" text |

---

## Integrations

### Calendar Providers

All calendar providers implement the `CalendarPort` protocol:

```python
class CalendarPort(Protocol):
    async def add_event(self, parsed_event: ParsedEvent) -> dict: ...
    async def find_events(self, query: str, target_date: str) -> list[dict]: ...
    async def delete_event(self, event_id: str) -> None: ...
    async def update_event(self, event_id: str, new_date: str, new_time: str) -> dict: ...
    async def add_recurring_event(self, ...) -> dict: ...
    async def get_daily_events(self, target_date: str) -> list[dict]: ...
    async def add_guests(self, event_id: str, guests: list[str]) -> dict: ...
    async def update_event_fields(self, event_id: str, **fields: Any) -> dict: ...
```

**Google Calendar** (`google_calendar.py`) — Uses Google Calendar API v3 via `google-api-python-client`. Auth handled by `google_auth.py` (OAuth2 flow with token persistence).

**Outlook/365** (`outlook_calendar.py`) — Uses Microsoft Graph SDK. Auth handled by `ms_auth.py`. Normalizes Graph `Event` objects to the same dict format as Google.

**CalDAV** (`caldav_calendar.py`) — Uses the `caldav` + `icalendar` libraries. Wraps sync calls with `asyncio.to_thread()` for async compatibility. Works with iCloud, Nextcloud, Fastmail, and any CalDAV-compliant server.

The factory (`calendar_factory.py`) reads `CALENDAR_PROVIDER` from config and returns the appropriate adapter.

### Google Maps Places API

`src/integrations/google_maps.py` enriches event locations:

1. User says "Coffee at Blue Bottle"
2. The `_enrich_location` enricher calls `enrich_location("Blue Bottle")`
3. Google Places API returns: display name, formatted address, and a Maps URL
4. The enriched location and Maps link are attached to the event

Graceful degradation: if `GOOGLE_MAPS_API_KEY` is not set or the API call fails, the raw location string is used as-is.

### LLM Providers

`src/core/llm.py` exposes a single function: `async complete(system, user_message, max_tokens) → str`

The provider is selected via `LLM_PROVIDER` env var and initialized lazily on first call:

| Provider | Default Model | Library |
|----------|--------------|---------|
| `gemini` | `gemini-2.0-flash` | `google-generativeai` |
| `anthropic` | `claude-haiku-4-5-20251001` | `anthropic` |
| `openai` | `gpt-4o-mini` | `openai` |
| `cohere` | `command-a-03-2025` | `cohere` |

Libraries are imported lazily — only the selected provider's SDK is loaded.

---

## Decoupling the UI

The Telegram bot (`src/bot/telegram_bot.py`) is a **thin rendering layer**. It has zero business logic — all decisions are made by `ActionService`.

### How it works

1. User sends text/voice → bot handler receives it
2. Handler calls `service.process_text(text)` or `service.resolve_conflict(pending, choice)`
3. `ActionService` returns a `ServiceResponse`
4. `_render_response()` maps the response type to Telegram UI elements

### State management

Interactive flows (conflicts, contact resolution, time slot selection) store pending state in `context.user_data`:

- `pending_event` — event waiting for conflict resolution
- `pending_contact` — contact waiting for email
- `pending_slot` — event waiting for time slot selection
- `last_event_context` — last successful event (for modify flow)
- `awaiting_contact_email` — flag to route next text to contact handler
- `awaiting_custom_time` — flag to route next text to custom time handler

### Swapping the UI

To add a new UI (e.g., web API, CLI, WhatsApp):

1. Create an `ActionService` instance with the appropriate `CalendarPort`
2. Call `process_text()`, `resolve_conflict()`, `resolve_contact()`, `select_slot()` etc.
3. Map `ServiceResponse` subclasses to your UI's elements

The bot never touches calendar APIs, LLM calls, or database operations directly.

---

## LLM-Agnostic Logic

### Parser prompt generation

The system prompt is **auto-generated from Pydantic model schemas**. This means:

- Adding a field to a model automatically updates the LLM prompt
- Adding a new intent model + registry entries regenerates the full prompt
- `prompt_hidden` fields (via `json_schema_extra={"prompt_hidden": True}`) are excluded from the prompt but available at runtime (used for bot-injected context like `event_id`)

### Schema-driven approach

```python
# These four dicts fully define the intent system:
INTENT_REGISTRY = {"create": ParsedEvent, "cancel": CancelEvent, ...}
_INTENT_LABELS  = {"create": "Create Calendar Event", ...}
_INTENT_TRIGGERS = {"create": "If the user wants to schedule/create...", ...}
_BEHAVIORAL_RULES = {"create": ["Rule 1", "Rule 2"], ...}
```

The prompt builder iterates over these to produce:
- A JSON schema section showing all possible intents and their fields
- Trigger descriptions telling the LLM when to use each intent
- Behavioral rules constraining LLM output per intent

### LLM matching

Beyond parsing, the LLM is used for fuzzy event matching:

- `match_event(description, events)` — "the dentist appointment" → finds the matching event from a list
- `batch_match_events(descriptions, events)` — matches multiple descriptions in one LLM call
- `batch_exclude_events(exceptions, events)` — identifies which events to keep when user says "cancel everything except..."

These functions are provider-agnostic — they all go through the same `complete()` interface.

---

## Data Layer

### SQLite Database

Two database classes in `src/data/db.py`:

**ChoreDB** — Manages recurring household chores:
- CRUD operations with soft deletes (`active=0`)
- `mark_done()` advances `next_due` based on `frequency_days`
- Safe migrations: checks column existence before `ALTER TABLE`
- Linked to calendar via `calendar_event_id`

**ContactDB** — Smart contact resolution:
- `add_contact(name, email)` — insert or update
- `find_by_name(name)` — case-insensitive lookup (uses `name_normalized`)
- Grows organically as users mention people by name

### Models

```python
@dataclass
class Chore:
    id, name, frequency_days, duration_minutes,
    preferred_time_start, preferred_time_end,
    next_due, assigned_to, last_done, calendar_event_id, active

@dataclass
class Contact:
    id, name, email, name_normalized
```

---

## Configuration

All configuration lives in `src/config.py` as a Pydantic `Settings` singleton loaded from `.env`:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram Bot API token |
| `LLM_API_KEY` | Yes | — | API key for the selected LLM provider |
| `LLM_PROVIDER` | No | `gemini` | `gemini`, `anthropic`, `openai`, or `cohere` |
| `LLM_MODEL` | No | auto | Override the default model for the provider |
| `CALENDAR_PROVIDER` | No | `google` | `google`, `outlook`, or `caldav` |
| `ALLOWED_USER_IDS` | No | `[]` | Comma-separated Telegram user IDs |
| `GOOGLE_MAPS_API_KEY` | No | `""` | Enables location enrichment |
| `OPENAI_API_KEY` | No | `""` | Enables voice transcription (Whisper) |
| `TIMEZONE` | No | `Asia/Jerusalem` | Timezone for all date/time operations |
| `MORNING_BRIEFING_HOUR` | No | `8` | Hour for daily schedule notification |
| `DATABASE_PATH` | No | `data/chores.db` | SQLite database file path |

Calendar-specific variables are only needed for the selected provider (see `.env` template in README).

---

## Testing

### Strategy

- **393+ tests**, all passing
- **No real API calls** — every external service is mocked
- **Class-based organization** — `class TestFoo:` groups related tests
- **Async-first** — `@pytest.mark.asyncio` with `asyncio_mode = "auto"`
- **Fixtures in `conftest.py`** — patches env vars before any `src` imports, provides temp DB instances

### Running tests

```bash
# Full suite
pytest -v

# Single module
pytest tests/test_parser.py -v

# Single test class
pytest tests/test_action_service.py::TestModifyEvent -v
```

### Test files

| File | Coverage |
|------|----------|
| `test_parser.py` | Intent models, registry, prompt generation, LLM matching |
| `test_action_service.py` | Full action flows: create, cancel, reschedule, query, modify, contacts, conflicts, slots, batch |
| `test_gcal_service.py` | Google Calendar adapter + `_build_event_body` |
| `test_outlook_calendar.py` | Outlook adapter operations |
| `test_caldav_calendar.py` | CalDAV adapter operations |
| `test_telegram_bot.py` | Bot handlers, rendering, authorization, callback flows |
| `test_conflict_checker.py` | Conflict detection + free slot finding |
| `test_chore_scheduler.py` | Recurring chore slot optimization |
| `test_calendar_factory.py` | Factory routing |
| `test_db.py` | ChoreDB CRUD + migrations |
| `test_contact_db.py` | ContactDB operations |
| `test_google_maps.py` | Location enrichment |
| `test_models.py` | Dataclass validation |

---

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`):

- **Trigger**: PR to main/master, push to main/master
- **Matrix strategy**: 10 test modules run in parallel (`fail-fast: false`)
- **Python 3.13** on `ubuntu-latest`
- **Gate job**: All matrix jobs must pass for the PR to be mergeable
- **Environment**: Fake tokens, in-memory SQLite (`DATABASE_PATH=:memory:`)
