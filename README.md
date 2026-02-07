# LifeOS Assistant

Your personal calendar assistant that lives inside Telegram. Talk to it like you'd talk to a friend — "Meeting with Dan tomorrow at 2pm" — and it handles the rest.

---

## What It Does

- **Create events** — "Coffee with Sarah on Friday at 10am at Blue Bottle"
- **Cancel events** — "Cancel the dentist appointment"
- **Reschedule events** — "Move the team sync to 3pm"
- **Check your schedule** — "What do I have tomorrow?"
- **Invite people** — "Add Amit to the meeting" (learns their email once, remembers forever)
- **Smart conflicts** — Warns you when a time slot is taken and suggests alternatives
- **Location lookup** — Validates addresses via Google Maps and adds a direct link
- **Modify events** — After creating an event, say "add location: the coffee shop" or "also invite Dan"
- **Voice messages** — Send a voice note instead of typing
- **Recurring chores** — Set up weekly chores that auto-schedule to open calendar slots
- **Morning briefing** — Get a daily summary of your schedule every morning

You can even do multiple things at once: "Cancel the dentist, create lunch with Dan at noon, and reschedule the standup to 4pm."

---

## Supported Calendar Services

| Service | Setup Effort |
|---------|-------------|
| **Google Calendar** | One-time auth with your Google account |
| **Outlook / Microsoft 365** | One-time auth with your Microsoft account |
| **CalDAV** (iCloud, Nextcloud, Fastmail) | Enter your CalDAV server URL + credentials |

You choose one when setting up. You can switch later by changing one setting.

---

## Setup Guide

### What You'll Need

1. **Python 3.11+** installed on your computer
2. A **Telegram account** (the free messaging app)
3. An API key from one of: Google AI (Gemini), Anthropic (Claude), OpenAI (ChatGPT), or Cohere
4. Calendar credentials (depends on which calendar you use)

### Step 1: Download the Project

```bash
git clone https://github.com/amitwitk/life-os-assistant.git
cd life-os-assistant
```

### Step 2: Install Dependencies

```bash
python -m venv venv
source venv/bin/activate      # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3: Create Your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts to name your bot
3. BotFather will give you a **token** — copy it (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
4. Find your Telegram user ID: search for **@userinfobot** in Telegram and send `/start`

### Step 4: Get an AI API Key

Pick one AI provider and get an API key:

- **Google Gemini** (free tier available): [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- **Anthropic Claude**: [https://console.anthropic.com/](https://console.anthropic.com/)
- **OpenAI**: [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- **Cohere**: [https://dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys)

### Step 5: Set Up Your Calendar

**Google Calendar:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the Google Calendar API
3. Create OAuth2 credentials and download as `credentials.json`
4. Place `credentials.json` in the project folder
5. On first run, a browser window will open for you to authorize access

**Outlook / Microsoft 365:**
1. Register an app in [Azure Portal](https://portal.azure.com/)
2. Note the Client ID, Client Secret, and Tenant ID

**CalDAV (iCloud, Nextcloud, etc.):**
1. Find your CalDAV server URL (check your provider's docs)
2. Have your username and password ready

### Step 6: Configure Settings

Create a file called `.env` in the project folder with these settings:

```bash
# Required
TELEGRAM_BOT_TOKEN=your-bot-token-from-step-3
LLM_API_KEY=your-ai-api-key-from-step-4

# AI provider (pick one: gemini, anthropic, openai, cohere)
LLM_PROVIDER=gemini

# Your Telegram user ID (only you can use the bot)
ALLOWED_USER_IDS=your-telegram-user-id

# Calendar provider (pick one: google, outlook, caldav)
CALENDAR_PROVIDER=google

# Optional: Google Maps for location lookup
# GOOGLE_MAPS_API_KEY=your-google-maps-key

# Optional: Voice message support
# OPENAI_API_KEY=your-openai-key

# Optional: Morning briefing time (24h format)
# MORNING_BRIEFING_HOUR=8
# TIMEZONE=Asia/Jerusalem
```

For Outlook, also add:
```bash
MS_CLIENT_ID=your-client-id
MS_CLIENT_SECRET=your-client-secret
MS_TENANT_ID=your-tenant-id
```

For CalDAV, also add:
```bash
CALDAV_URL=https://your-server.com/dav
CALDAV_USERNAME=your-username
CALDAV_PASSWORD=your-password
CALDAV_CALENDAR_NAME=Personal
```

### Step 7: Start the Bot

```bash
python main.py
```

Open Telegram, find your bot, and send `/start`. You're ready to go!

---

## Daily Usage

### Creating Events

Just type naturally:

- "Meeting tomorrow at 2pm"
- "Dinner with Sarah on Friday at 7pm for 2 hours"
- "Dentist on Feb 14 at 10:30"
- "Coffee at Blue Bottle next Monday at 9am"

If you don't specify a time, the bot will suggest open slots on your calendar.

### Mentioning People

Say someone's name and the bot will invite them:

- "Lunch with Dan tomorrow at noon"

The first time, it will ask for Dan's email. After that, it remembers.

### Modifying Events

Right after creating or rescheduling an event, you can tweak it:

- "Add location: Blue Bottle Coffee"
- "Also invite Sarah"
- "Change time to 3pm"
- "Add description: bring the quarterly report"

### Managing Your Day

- `/today` — See today's schedule
- "What's on my calendar for Friday?"
- "Cancel everything except the dentist"

### Recurring Chores

- `/addchore` — Set up a recurring task (the bot walks you through it)
- `/listchores` — See all your chores
- `/deletechore` — Remove a chore
- `/done` — Mark a chore as completed

### Voice Messages

Send a voice note in Telegram and the bot transcribes it and acts on it. Requires an OpenAI API key in your settings (for Whisper transcription).

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check that your `ALLOWED_USER_IDS` matches your Telegram user ID |
| "TELEGRAM_BOT_TOKEN is missing" | Make sure your `.env` file exists and has the correct token |
| "LLM_API_KEY is missing" | Add your AI provider's API key to `.env` |
| Google Calendar auth fails | Make sure `credentials.json` is in the project folder |
| Events not showing up | Check that `CALENDAR_PROVIDER` matches your calendar service |
| Location links not working | Add `GOOGLE_MAPS_API_KEY` to your `.env` |
| Voice messages not working | Add `OPENAI_API_KEY` to your `.env` |

### Getting Help

If something isn't working, check the terminal where you ran `python main.py` — error messages appear there with details about what went wrong.

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

---

## Tech Stack

For the curious: Python 3.13, python-telegram-bot, Pydantic, SQLite, Google Calendar API, Microsoft Graph, CalDAV, and your choice of AI provider (Gemini, Claude, GPT, or Cohere). Hexagonal architecture with 393+ automated tests.
