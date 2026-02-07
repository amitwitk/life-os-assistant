# LifeOS Assistant — Skills Library

Reusable patterns extracted from the codebase. Use these templates when adding new capabilities (e.g., expense tracking, habit tracking, note-taking, grocery lists).

---

## Table of Contents

- [Skill 1: Add a New Intent](#skill-1-add-a-new-intent)
- [Skill 2: Add a New Calendar Adapter](#skill-2-add-a-new-calendar-adapter)
- [Skill 3: Add an Enricher Step](#skill-3-add-an-enricher-step)
- [Skill 4: Add a New Response Type](#skill-4-add-a-new-response-type)
- [Skill 5: Add a Pending Resolution Flow](#skill-5-add-a-pending-resolution-flow)
- [Skill 6: Add a New LLM Provider](#skill-6-add-a-new-llm-provider)
- [Skill 7: Add a Bot Command](#skill-7-add-a-bot-command)
- [Skill 8: Add a Callback Button Flow](#skill-8-add-a-callback-button-flow)
- [Skill 9: Add a Database Table](#skill-9-add-a-database-table)
- [Skill 10: Write Tests for a New Feature](#skill-10-write-tests-for-a-new-feature)

---

## Skill 1: Add a New Intent

When you want the LLM to recognize a new kind of user request.

### Files to change

- `src/core/parser.py` — Model + registry entries
- `src/core/action_service.py` — Execution handler + `_execute_single_action` branch
- `tests/test_parser.py` — Model tests
- `tests/test_action_service.py` — Flow tests

### Template

**Step 1: Define the Pydantic model** in `parser.py`:

```python
class MyNewIntent(BaseModel):
    """One-line description of what this intent does."""
    intent: str = Field(default="my_intent", description="Action type — always 'my_intent'")
    # LLM-visible fields:
    target: str = Field(default="", description="What the user wants to act on")
    value: str = Field(default="", description="The value to set")
    # Bot-injected fields (hidden from LLM prompt):
    context_id: str = Field(default="", json_schema_extra={"prompt_hidden": True})

    @property
    def log_summary(self) -> str:
        return f"my_intent: target={self.target}, value={self.value}"
```

**Step 2: Register** in four dictionaries:

```python
# In INTENT_REGISTRY:
"my_intent": MyNewIntent,

# In _INTENT_LABELS:
"my_intent": "My New Intent",

# In _INTENT_TRIGGERS:
"my_intent": 'If the user wants to <description of when to use this intent>',

# In _BEHAVIORAL_RULES:
"my_intent": [
    "Rule 1: when to use vs. not use this intent.",
    "Rule 2: field-specific guidance for the LLM.",
],
```

**Step 3: Update `ParserResponse`** union type:

```python
ParserResponse = ParsedEvent | CancelEvent | ... | MyNewIntent
```

**Step 4: Add execution handler** in `action_service.py`:

```python
async def _execute_my_intent(self, parsed: MyNewIntent) -> ServiceResponse:
    # Business logic here
    try:
        result = await self._calendar.some_method(...)
        return SuccessResponse(
            kind=ResponseKind.SUCCESS,
            message=f"Done: {parsed.log_summary}",
            event=EventInfo(summary=parsed.target, ...),
        )
    except CalendarError as exc:
        return ErrorResponse(kind=ResponseKind.ERROR, message="Failed.")
```

**Step 5: Wire into `_execute_single_action`**:

```python
if isinstance(action, MyNewIntent):
    return await self._execute_my_intent(action)
```

### Checklist

- [ ] Model has `intent` field with correct default
- [ ] Model has `log_summary` property
- [ ] All four registry dicts updated
- [ ] `ParserResponse` union includes the new model
- [ ] `_execute_single_action` has the new branch
- [ ] Tests for model defaults, log_summary, and execution flow

---

## Skill 2: Add a New Calendar Adapter

When you want to support a new calendar service (e.g., Notion Calendar, Todoist).

### Files to change

- `src/adapters/new_calendar.py` — New adapter file
- `src/adapters/calendar_factory.py` — Factory branch
- `src/config.py` — Provider-specific env vars
- `tests/test_new_calendar.py` — Adapter tests

### Template

**Step 1: Create the adapter** implementing `CalendarPort`:

```python
"""LifeOS Assistant — NewService calendar adapter."""
from __future__ import annotations

import logging
from src.ports.calendar_port import CalendarError

logger = logging.getLogger(__name__)


class NewCalendarAdapter:
    """CalendarPort implementation for NewService."""

    def __init__(self) -> None:
        from src.config import settings
        # Initialize client using settings
        self._client = self._build_client(settings)

    async def add_event(self, parsed_event) -> dict:
        try:
            # Convert ParsedEvent to provider's format
            result = await self._client.create(...)
            return {"id": result.id, "htmlLink": result.url, "summary": parsed_event.event}
        except Exception as exc:
            logger.error("NewService add_event failed: %s", exc)
            raise CalendarError(f"Failed to create event: {exc}") from exc

    async def find_events(self, query: str, target_date: str) -> list[dict]:
        # Return list of {"id", "summary", "start", "end", "description"}
        ...

    async def delete_event(self, event_id: str) -> None: ...
    async def update_event(self, event_id: str, new_date: str, new_time: str) -> dict: ...
    async def add_recurring_event(self, ...) -> dict: ...
    async def get_daily_events(self, target_date: str) -> list[dict]: ...
    async def add_guests(self, event_id: str, guests: list[str]) -> dict: ...
    async def update_event_fields(self, event_id: str, **fields) -> dict: ...
```

**Step 2: Add to factory** in `calendar_factory.py`:

```python
elif provider == "newservice":
    from src.adapters.new_calendar import NewCalendarAdapter
    return NewCalendarAdapter()
```

**Step 3: Add config vars** in `config.py`:

```python
# NewService (only needed when CALENDAR_PROVIDER=newservice)
NEWSERVICE_API_KEY: str = ""
NEWSERVICE_WORKSPACE_ID: str = ""
```

### Key patterns

- All methods are `async` — use `asyncio.to_thread()` if wrapping a sync library
- Normalize return dicts to: `{"id", "summary", "start", "end", "description", "htmlLink"}`
- Wrap all provider exceptions in `CalendarError`
- Use deferred imports for provider-specific libraries

---

## Skill 3: Add an Enricher Step

When you want to transform or validate parsed events before execution.

### Files to change

- `src/core/action_service.py` — New enricher method + pipeline registration

### Template

```python
async def _enrich_my_step(self, parsed: object) -> object | ServiceResponse:
    """Enrich parsed event with additional data."""
    if not isinstance(parsed, ParsedEvent):
        return parsed  # Only enriches create intents

    # Do your enrichment
    enriched_value = await some_external_call(parsed.some_field)

    if enriched_value is None:
        return parsed  # Graceful degradation — skip enrichment

    # Return transformed ParsedEvent
    return parsed.model_copy(update={"some_field": enriched_value})
```

**Register in the pipeline:**

```python
_CREATE_PIPELINE = [
    self._enrich_contacts,
    self._enrich_time,
    self._enrich_location,
    self._enrich_my_step,      # <-- add here
    self._enrich_conflicts,     # conflicts should stay last
]
```

### Enricher contract

- **Input**: A parsed object (usually `ParsedEvent`)
- **Output**: Either the enriched object (continue pipeline) or a `ServiceResponse` (pause and ask user)
- **Guard**: Check `isinstance` at the top — return unchanged for non-matching types
- **Graceful**: Never crash on enrichment failure; return the original object

---

## Skill 4: Add a New Response Type

When `ActionService` needs to communicate a new kind of result to the UI.

### Files to change

- `src/core/action_service.py` — New response dataclass
- `src/bot/telegram_bot.py` — New rendering branch in `_render_response`

### Template

**Step 1: Define the response** in `action_service.py`:

```python
@dataclass
class MyPromptResponse(ServiceResponse):
    """Prompt the user for additional information."""
    kind: ResponseKind = ResponseKind.MY_PROMPT  # add to ResponseKind enum
    message: str = ""
    options: list[str] = field(default_factory=list)
    pending: MyPendingData | None = None
```

**Step 2: Add rendering** in `telegram_bot.py`:

```python
elif isinstance(response, MyPromptResponse):
    context.user_data["pending_my_data"] = response.pending
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"myprompt:{opt}")]
        for opt in response.options
    ]
    await update.message.reply_text(
        response.message, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
```

---

## Skill 5: Add a Pending Resolution Flow

When the user needs to answer a question before the action can complete (like contact resolution or conflict resolution).

### Pattern overview

1. **ActionService** detects missing info → returns a prompt response with a `Pending*` dataclass
2. **Bot** stores the pending object in `context.user_data["pending_xxx"]`
3. **User** responds (button click or text input)
4. **Bot** calls `service.resolve_xxx(pending, user_input)`
5. **ActionService** resumes execution with the missing info filled in

### Template

**Step 1: Define pending data** in `action_service.py`:

```python
@dataclass
class PendingMyResolution:
    action_type: str                              # "create" | "modify" | ...
    parsed_action_json: dict = field(default_factory=dict)
    resolved_items: dict = field(default_factory=dict)
    unresolved_items: list = field(default_factory=list)
    current_asking: str = ""
```

**Step 2: Detection** (in enricher or execute method):

```python
if unresolved:
    pending = PendingMyResolution(
        action_type="create",
        parsed_action_json=parsed.model_dump(),
        unresolved_items=unresolved,
        current_asking=unresolved[0],
    )
    return MyPromptResponse(
        kind=ResponseKind.MY_PROMPT,
        message=f"I need to know about *{unresolved[0]}*. What's the value?",
        pending=pending,
    )
```

**Step 3: Resolution** method:

```python
async def resolve_my_item(self, pending: PendingMyResolution, value: str) -> ServiceResponse:
    # Validate
    if not is_valid(value):
        return MyPromptResponse(message=f"'{value}' is not valid. Try again.", pending=pending)

    # Save to DB if needed
    self._db.save(pending.current_asking, value)

    # Move from unresolved → resolved
    pending.resolved_items[pending.current_asking] = value
    pending.unresolved_items.remove(pending.current_asking)

    # More to resolve?
    if pending.unresolved_items:
        pending.current_asking = pending.unresolved_items[0]
        return MyPromptResponse(
            message=f"What about *{pending.current_asking}*?",
            pending=pending,
        )

    # All resolved — re-execute original action
    parsed_data = pending.parsed_action_json
    # ... merge resolved values into parsed_data ...
    parsed = ParsedEvent(**parsed_data)
    return await self._execute_single_action(parsed)
```

**Step 4: Bot handler**:

```python
async def _handle_my_resolution(text, update, context):
    pending = context.user_data.get("pending_my_data")
    if not pending:
        await update.message.reply_text("Nothing pending.")
        return
    service = context.bot_data["action_service"]
    response = await service.resolve_my_item(pending, text.strip())
    if isinstance(response, MyPromptResponse):
        context.user_data["pending_my_data"] = response.pending
    else:
        context.user_data.pop("pending_my_data", None)
        await _render_response(response, update, context)
```

---

## Skill 6: Add a New LLM Provider

### Files to change

- `src/core/llm.py` — New completion function + provider routing

### Template

```python
async def _complete_newprovider(system: str, user_message: str, max_tokens: int) -> str:
    """Complete using NewProvider API."""
    from newprovider import AsyncClient  # deferred import

    from src.config import settings
    client = AsyncClient(api_key=settings.LLM_API_KEY)
    model = settings.LLM_MODEL or "newprovider-default-model"

    response = await client.chat(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
    )
    return response.text
```

Then add to the provider routing in `complete()`:

```python
elif provider == "newprovider":
    _complete_fn = _complete_newprovider
```

### Checklist

- [ ] Deferred import of the provider SDK
- [ ] Reads `settings.LLM_MODEL` with a sensible default
- [ ] Returns raw text (no parsing)
- [ ] Uses `settings.LLM_API_KEY`

---

## Skill 7: Add a Bot Command

### Files to change

- `src/bot/telegram_bot.py` — Handler function + registration in `build_app()`

### Template

```python
@authorized_only
async def cmd_mycommand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mycommand — description."""
    service: ActionService = context.bot_data["action_service"]

    try:
        result = await service.some_method()
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception:
        logger.exception("cmd_mycommand failed")
        await update.message.reply_text("Something went wrong.")
```

Register in `build_app()`:

```python
app.add_handler(CommandHandler("mycommand", cmd_mycommand))
```

### Key patterns

- Always use `@authorized_only` decorator
- Access service via `context.bot_data["action_service"]`
- Use `parse_mode="Markdown"` for formatted output
- Log exceptions with `logger.exception()`

---

## Skill 8: Add a Callback Button Flow

### Files to change

- `src/bot/telegram_bot.py` — Callback handler + button creation

### Template

**Create buttons** (in a command or response renderer):

```python
buttons = [
    [InlineKeyboardButton("Option A", callback_data="myflow:optionA")],
    [InlineKeyboardButton("Option B", callback_data="myflow:optionB")],
]
await update.message.reply_text(
    "Choose an option:",
    reply_markup=InlineKeyboardMarkup(buttons),
)
```

**Handle callback:**

```python
@authorized_only
async def _handle_myflow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action = query.data.split(":")[1]  # "optionA" or "optionB"

    if action == "optionA":
        await query.edit_message_text("You chose Option A!")
    elif action == "optionB":
        await query.edit_message_text("You chose Option B!")
```

**Register** in `build_app()`:

```python
app.add_handler(CallbackQueryHandler(_handle_myflow_callback, pattern=r"^myflow:"))
```

### Callback data format

Convention: `"prefix:value"` — prefix identifies the flow, value identifies the choice. Parse with `query.data.split(":")`.

---

## Skill 9: Add a Database Table

### Files to change

- `src/data/models.py` — New dataclass
- `src/data/db.py` — New DB class

### Template

**Model** in `models.py`:

```python
@dataclass
class MyItem:
    id: int
    name: str
    value: str
    active: bool = field(default=True)  # soft delete
```

**DB class** in `db.py`:

```python
class MyItemDB:
    """SQLite operations for MyItem."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            from src.config import settings
            db_path = settings.DATABASE_PATH
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS my_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            # Safe migration example:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(my_items)")}
            if "new_column" not in cols:
                conn.execute("ALTER TABLE my_items ADD COLUMN new_column TEXT DEFAULT ''")

    def add(self, name: str, value: str) -> int:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO my_items (name, value) VALUES (?, ?)",
                (name, value),
            )
            return cursor.lastrowid

    def get(self, item_id: int) -> MyItem | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM my_items WHERE id = ? AND active = 1", (item_id,)
            ).fetchone()
            return MyItem(**dict(row)) if row else None

    def delete(self, item_id: int) -> None:
        """Soft delete."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("UPDATE my_items SET active = 0 WHERE id = ?", (item_id,))
```

### Key patterns

- Soft deletes: `active=0` instead of `DELETE`
- Safe migrations: check column existence before `ALTER TABLE`
- Auto-create parent directories for DB file
- Use `sqlite3.Row` for dict-like row access

---

## Skill 10: Write Tests for a New Feature

### Files to change

- `tests/test_<module>.py` — New test class

### Template

```python
"""Tests for <feature>."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_service(calendar=None):
    """Create an ActionService with a mock calendar."""
    cal = calendar or MagicMock()
    from src.core.action_service import ActionService
    return ActionService(cal), cal


class TestMyFeature:
    """Tests for the new feature flow."""

    @pytest.mark.asyncio
    async def test_success_case(self):
        service, cal = _make_service()
        cal.some_method = AsyncMock(return_value={"id": "1"})

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("do something")

        assert isinstance(response, SuccessResponse)
        assert "expected text" in response.message
        cal.some_method.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_case(self):
        service, cal = _make_service()
        cal.some_method = AsyncMock(side_effect=CalendarError("fail"))

        with patch("src.core.parser.parse_message", AsyncMock(return_value=[parsed])):
            response = await service.process_text("do something")

        assert isinstance(response, ErrorResponse)

    @pytest.mark.asyncio
    async def test_pending_resolution(self):
        service, cal = _make_service()
        # ... setup mock to trigger pending state ...
        response = await service.process_text("do something")
        assert isinstance(response, MyPromptResponse)

        # Resolve
        resolved = await service.resolve_my_item(response.pending, "user_input")
        assert isinstance(resolved, SuccessResponse)
```

### Test conventions

- Class-based: `class TestFeatureName:`
- `@pytest.mark.asyncio` on all async tests
- Use `AsyncMock` for async methods, `MagicMock` for sync
- Use `patch()` context manager for scoped mocking
- Assert response type with `isinstance()`
- Verify mock calls with `.assert_called_once()` / `.assert_called_once_with()`
- Use `_make_service()` helper for ActionService with mock calendar
- No real I/O in tests — mock everything external
