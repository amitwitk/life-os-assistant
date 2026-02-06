# LifeOS Assistant - Project Dashboard

## The 4 Pillars
1. **Capture System** — Text or voice → parsed event → Google Calendar. The fastest path from thought to scheduled event.
2. **Morning Briefing** — Proactive daily push at 08:00 (Asia/Jerusalem) with calendar + chores, summarized by Claude.
3. **Memory** — Chores persist in SQLite across days. Events live in Google Calendar. Two stores, one assistant.
4. **Integration Stack** — Telegram is the only UI. Claude is the brain. Whisper handles audio. Google Calendar is the source of truth.

## Architecture
```
Text message ──→ Claude Parser ──→ ParsedEvent JSON ──→ Google Calendar
Voice message ─→ Whisper STT ──↗

/addchore ──→ SQLite (ChoreDB)

Scheduler (08:00 daily)
  ├─ reads Google Calendar
  ├─ reads SQLite chores
  └─→ Claude summary ──→ Telegram message
```

## Tech Stack
- **Language:** Python 3.11+
- **Bot:** python-telegram-bot v20+
- **LLM:** Anthropic Claude (parsing + summarization)
- **Audio:** OpenAI Whisper (transcription only)
- **Calendar:** Google Calendar API v3
- **Database:** SQLite
- **Scheduler:** python-telegram-bot JobQueue

---

## Phase 1: Core Infrastructure
- [[1.1_Environment_Setup]]
- [[1.2_LLM_Parser]]

## Phase 2: Google Calendar Integration
- [[2.1_Auth_Script]]
- [[2.2_Event_Writer]]
- [[2.3_Event_Reader]]

## Phase 3: Telegram Interface
- [[3.1_Bot_Basic]]
- [[3.2_Voice_Handling]]
- [[3.3_Main_Logic]]

## Phase 4: Automation & Chores
- [[4.1_Database_Setup]]
- [[4.2_Scheduler]]

---

## Progress Tracker
| Phase | Tasks | Status |
|-------|-------|--------|
| Phase 1 - Core | 2 | Not Started |
| Phase 2 - Calendar | 3 | Not Started |
| Phase 3 - Telegram | 3 | Not Started |
| Phase 4 - Automation | 2 | Not Started |
| **Total** | **10** | **0/10 Complete** |
