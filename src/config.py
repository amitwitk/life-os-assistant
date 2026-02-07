"""
LifeOS Assistant — Centralized configuration.

Loads all settings from .env and validates required keys.
This module is the foundation for every other module in the project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

# Load .env from project root (two levels up from src/config.py)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str

    # LLM — provider-agnostic (gemini, anthropic, openai)
    LLM_PROVIDER: str = "gemini"
    LLM_MODEL: str = ""          # empty → smart default per provider
    LLM_API_KEY: str

    # Audio — OpenAI Whisper (transcription only)
    OPENAI_API_KEY: str = ""

    # Calendar provider: "google" | "outlook" | "caldav"
    CALENDAR_PROVIDER: str = "google"

    # Google Calendar (only needed when CALENDAR_PROVIDER=google)
    GOOGLE_CREDENTIALS_PATH: str = "credentials.json"
    GOOGLE_TOKEN_PATH: str = "token.json"

    # Microsoft Outlook/365 (only needed when CALENDAR_PROVIDER=outlook)
    MS_CLIENT_ID: str = ""
    MS_CLIENT_SECRET: str = ""
    MS_TENANT_ID: str = "common"
    MS_TOKEN_PATH: str = "ms_token.json"

    # CalDAV (only needed when CALENDAR_PROVIDER=caldav)
    CALDAV_URL: str = ""
    CALDAV_USERNAME: str = ""
    CALDAV_PASSWORD: str = ""
    CALDAV_CALENDAR_NAME: str = ""

    # SQLite
    DATABASE_PATH: str = "data/chores.db"

    # Security
    ALLOWED_USER_IDS: list[int] = []

    # Morning Briefing
    MORNING_BRIEFING_HOUR: int = 8
    TIMEZONE: str = "Asia/Jerusalem"

    # Google Maps Places API (optional — location enrichment)
    GOOGLE_MAPS_API_KEY: str = ""

    @field_validator("ALLOWED_USER_IDS", mode="before")
    @classmethod
    def parse_user_ids(cls, v: str | list[int]) -> list[int]:
        if isinstance(v, list):
            return v
        if isinstance(v, str) and v.strip():
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        return []

    @field_validator("MORNING_BRIEFING_HOUR", mode="before")
    @classmethod
    def parse_hour(cls, v: str | int) -> int:
        return int(v)


def _load_settings() -> Settings:
    """Load settings from environment, validating required keys."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    llm_api_key = os.getenv("LLM_API_KEY", "")

    if not token or token.startswith("your-"):
        print("ERROR: TELEGRAM_BOT_TOKEN is missing or not set in .env", file=sys.stderr)
        sys.exit(1)

    if not llm_api_key or llm_api_key.startswith("your-"):
        print("ERROR: LLM_API_KEY is missing or not set in .env", file=sys.stderr)
        sys.exit(1)

    return Settings(
        TELEGRAM_BOT_TOKEN=token,
        LLM_PROVIDER=os.getenv("LLM_PROVIDER", "gemini"),
        LLM_MODEL=os.getenv("LLM_MODEL", ""),
        LLM_API_KEY=llm_api_key,
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        CALENDAR_PROVIDER=os.getenv("CALENDAR_PROVIDER", "google"),
        GOOGLE_CREDENTIALS_PATH=os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        GOOGLE_TOKEN_PATH=os.getenv("GOOGLE_TOKEN_PATH", "token.json"),
        MS_CLIENT_ID=os.getenv("MS_CLIENT_ID", ""),
        MS_CLIENT_SECRET=os.getenv("MS_CLIENT_SECRET", ""),
        MS_TENANT_ID=os.getenv("MS_TENANT_ID", "common"),
        MS_TOKEN_PATH=os.getenv("MS_TOKEN_PATH", "ms_token.json"),
        CALDAV_URL=os.getenv("CALDAV_URL", ""),
        CALDAV_USERNAME=os.getenv("CALDAV_USERNAME", ""),
        CALDAV_PASSWORD=os.getenv("CALDAV_PASSWORD", ""),
        CALDAV_CALENDAR_NAME=os.getenv("CALDAV_CALENDAR_NAME", ""),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "data/chores.db"),
        ALLOWED_USER_IDS=os.getenv("ALLOWED_USER_IDS", ""),
        MORNING_BRIEFING_HOUR=os.getenv("MORNING_BRIEFING_HOUR", "8"),
        TIMEZONE=os.getenv("TIMEZONE", "Asia/Jerusalem"),
        GOOGLE_MAPS_API_KEY=os.getenv("GOOGLE_MAPS_API_KEY", ""),
    )


# Singleton — imported by all other modules as:
#   from src.config import settings
settings = _load_settings()
