"""Tests for src.data.models — Chore dataclass."""

from dataclasses import asdict

from src.data.models import Chore


def test_chore_creation_with_all_fields():
    chore = Chore(
        id=1,
        name="Clean kitchen",
        frequency_days=3,
        duration_minutes=30,
        preferred_time_start="17:00",
        preferred_time_end="21:00",
        next_due="2026-02-07",
        assigned_to="Amit",
    )
    assert chore.name == "Clean kitchen"
    assert chore.frequency_days == 3
    assert chore.duration_minutes == 30
    assert chore.preferred_time_start == "17:00"
    assert chore.preferred_time_end == "21:00"
    assert chore.last_done is None
    assert chore.calendar_event_id is None
    assert chore.active is True


def test_chore_defaults():
    chore = Chore(
        id=2,
        name="Trash",
        frequency_days=7,
        duration_minutes=15,
        preferred_time_start="09:00",
        preferred_time_end="12:00",
        next_due="2026-02-07",
        assigned_to="Dana",
    )
    assert chore.last_done is None
    assert chore.calendar_event_id is None
    assert chore.active is True


def test_chore_with_calendar_event_id():
    chore = Chore(
        id=3,
        name="Vacuum",
        frequency_days=7,
        duration_minutes=45,
        preferred_time_start="10:00",
        preferred_time_end="14:00",
        next_due="2026-02-10",
        assigned_to="Amit",
        calendar_event_id="gcal_abc123",
    )
    assert chore.calendar_event_id == "gcal_abc123"


def test_chore_serializable():
    chore = Chore(
        id=1, name="Test", frequency_days=1, duration_minutes=10,
        preferred_time_start="08:00", preferred_time_end="09:00",
        next_due="2026-01-01", assigned_to="Me",
    )
    d = asdict(chore)
    assert d["name"] == "Test"
    assert d["active"] is True
