"""Shared test fixtures and configuration.

Sets up fake environment variables so src.config doesn't sys.exit(),
and provides common fixtures like a temp DB.
"""

import os

# Patch env vars BEFORE any src imports
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake-token-for-tests")
os.environ.setdefault("LLM_API_KEY", "fake-llm-key-for-tests")
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("ALLOWED_USER_IDS", "12345")
os.environ.setdefault("DATABASE_PATH", ":memory:")

import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary SQLite DB path."""
    return str(tmp_path / "test_chores.db")


@pytest.fixture
def chore_db(tmp_db_path):
    """Return a ChoreDB instance backed by a temp file."""
    from src.data.db import ChoreDB
    return ChoreDB(db_path=tmp_db_path)
