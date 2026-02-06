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

    # LLM — Anthropic Claude (parsing + summarization)
    ANTHROPIC_API_KEY: str

    # Audio — OpenAI Whisper (transcription only)
    OPENAI_API_KEY: str = ""

    # Google Calendar
    GOOGLE_CREDENTIALS_PATH: str = "credentials.json"
    GOOGLE_TOKEN_PATH: str = "token.json"

    # SQLite
    DATABASE_PATH: str = "data/chores.db"

    # Security
    ALLOWED_USER_IDS: list[int] = []

    # Morning Briefing
    MORNING_BRIEFING_HOUR: int = 8
    TIMEZONE: str = "Asia/Jerusalem"

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
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not token or token.startswith("your-"):
        print("ERROR: TELEGRAM_BOT_TOKEN is missing or not set in .env", file=sys.stderr)
        sys.exit(1)

    if not anthropic_key or anthropic_key.startswith("your-"):
        print("ERROR: ANTHROPIC_API_KEY is missing or not set in .env", file=sys.stderr)
        sys.exit(1)

    return Settings(
        TELEGRAM_BOT_TOKEN=token,
        ANTHROPIC_API_KEY=anthropic_key,
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        GOOGLE_CREDENTIALS_PATH=os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        GOOGLE_TOKEN_PATH=os.getenv("GOOGLE_TOKEN_PATH", "token.json"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "data/chores.db"),
        ALLOWED_USER_IDS=os.getenv("ALLOWED_USER_IDS", ""),
        MORNING_BRIEFING_HOUR=os.getenv("MORNING_BRIEFING_HOUR", "8"),
        TIMEZONE=os.getenv("TIMEZONE", "Asia/Jerusalem"),
    )


# Singleton — imported by all other modules as:
#   from src.config import settings
settings = _load_settings()
