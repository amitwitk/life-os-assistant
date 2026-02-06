⏺ Here's everything you need to do, in order:                                                                                                                               
  ---                                                                                                                       
  Step 1: Get your 3 API keys                                                                                               
  1a. Telegram Bot Token                                                                                                                             
  1. Open Telegram, search for @BotFather
  2. Send /newbot, give it a name (e.g. "LifeOS Assistant")
  3. Copy the token it gives you

  1b. Anthropic API Key

  4. Go to https://console.anthropic.com/settings/keys
  5. Create a new API key
  6. Copy it

  1c. OpenAI API Key (for Whisper voice transcription)

  7. Go to https://platform.openai.com/api-keys
  8. Create a new key
  9. Copy it

  If you don't plan to use voice messages, you can skip this and leave it blank — text will still work.

  ---
  Step 2: Google Calendar credentials

  10. Go to https://console.cloud.google.com/
  11. Create a new project (or use an existing one)
  12. Enable the Google Calendar API (APIs & Services → Library → search "Calendar")
  13. Go to APIs & Services → Credentials
  14. Click Create Credentials → OAuth client ID
  15. Application type: Desktop app
  16. Download the JSON file
  17. Save it as credentials.json in the project root (/Users/amitwitk/Projects/Thachles-mazcira/credentials.json)

  ---
  Step 3: Get your Telegram user ID

  18. Open Telegram, search for @userinfobot
  19. Send it any message — it will reply with your user ID (a number like 123456789)
  20. Note this down

  ---
  Step 4: Fill in the .env file

  Edit /Users/amitwitk/Projects/Thachles-mazcira/.env:

  TELEGRAM_BOT_TOKEN=<paste from step 1a>
  ANTHROPIC_API_KEY=<paste from step 1b>
  OPENAI_API_KEY=<paste from step 1c>
  GOOGLE_CREDENTIALS_PATH=credentials.json
  GOOGLE_TOKEN_PATH=token.json
  DATABASE_PATH=data/chores.db
  ALLOWED_USER_IDS=<your user ID from step 3>
  MORNING_BRIEFING_HOUR=08
  TIMEZONE=Asia/Jerusalem

  ---
  Step 5: Install dependencies

  cd /Users/amitwitk/Projects/Thachles-mazcira
  source venv/bin/activate
  pip install -r requirements.txt

  The venv already exists from our setup, so this is mostly a no-op.

  ---
  Step 6: Authorize Google Calendar (one-time)

  cd /Users/amitwitk/Projects/Thachles-mazcira
  source venv/bin/activate
  python -m src.integrations.google_auth

  This will open a browser window asking you to log in to Google and grant calendar access. After approval, a token.json
  file is saved — you won't need to do this again.

  ---
  Step 7: Run the bot

  cd /Users/amitwitk/Projects/Thachles-mazcira
  source venv/bin/activate
  python main.py

  ---
  Step 8: Test it

  Open Telegram, find your bot, and try:
  ┌──────────────────────────────────────────┬───────────────────────────────────────────────────────────────────┐
  │                What to do                │                          Expected result                          │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send: Meeting with Dan tomorrow at 14:00 │ Creates a Google Calendar event, confirms with link               │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send a voice message saying the same     │ Transcribes → parses → creates event                              │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send: /today                             │ Shows today's calendar events                                     │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send: /addchore                          │ Walks you through adding a chore (name → frequency → assigned to) │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send: /chores                            │ Lists active chores                                               │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Send: /done 1                            │ Marks chore #1 as done                                            │
  ├──────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
  │ Wait until 08:00                         │ Receive a Claude-written morning briefing in Hebrew               │
  └──────────────────────────────────────────┴───────────────────────────────────────────────────────────────────┘
  ---
  Summary of external accounts needed
  ┌──────────────┬──────────────────────────────────────────┬───────────────────────────────────┐
  │   Service    │              What you need               │            Free tier?             │
  ├──────────────┼──────────────────────────────────────────┼───────────────────────────────────┤
  │ Telegram     │ Bot token via @BotFather                 │ Yes, free                         │
  ├──────────────┼──────────────────────────────────────────┼───────────────────────────────────┤
  │ Anthropic    │ API key                                  │ Pay-per-use (Haiku is very cheap) │
  ├──────────────┼──────────────────────────────────────────┼───────────────────────────────────┤
  │ OpenAI       │ API key (Whisper only)                   │ Pay-per-use                       │
  ├──────────────┼──────────────────────────────────────────┼───────────────────────────────────┤
  │ Google Cloud │ OAuth credentials + Calendar API enabled │ Yes, free for personal use        │