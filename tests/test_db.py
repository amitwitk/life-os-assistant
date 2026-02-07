"""Tests for src.data.db — ChoreDB (SQLite storage)."""

import pytest
from datetime import date, timedelta

from src.data.db import ChoreDB


class TestChoreDBAddAndList:
    def test_add_chore_returns_chore(self, chore_db):
        chore = chore_db.add_chore(
            name="Take out trash",
            frequency_days=7,
            assigned_to="Amit",
        )
        assert chore.id is not None
        assert chore.name == "Take out trash"
        assert chore.frequency_days == 7
        assert chore.assigned_to == "Amit"
        assert chore.active is True

    def test_add_chore_with_scheduling_fields(self, chore_db):
        chore = chore_db.add_chore(
            name="Vacuum",
            frequency_days=3,
            assigned_to="Dana",
            duration_minutes=45,
            preferred_time_start="17:00",
            preferred_time_end="20:00",
        )
        assert chore.duration_minutes == 45
        assert chore.preferred_time_start == "17:00"
        assert chore.preferred_time_end == "20:00"

    def test_add_chore_defaults(self, chore_db):
        chore = chore_db.add_chore(
            name="Dishes",
            frequency_days=1,
            assigned_to="Me",
        )
        assert chore.duration_minutes == 30
        assert chore.preferred_time_start == "09:00"
        assert chore.preferred_time_end == "21:00"
        assert chore.next_due == date.today().isoformat()

    def test_list_all_returns_added_chores(self, chore_db):
        chore_db.add_chore(name="A", frequency_days=1, assigned_to="X")
        chore_db.add_chore(name="B", frequency_days=2, assigned_to="Y")
        chores = chore_db.list_all()
        assert len(chores) == 2
        assert {c.name for c in chores} == {"A", "B"}

    def test_list_all_active_only(self, chore_db):
        c = chore_db.add_chore(name="A", frequency_days=1, assigned_to="X")
        chore_db.add_chore(name="B", frequency_days=2, assigned_to="Y")
        chore_db.delete_chore(c.id)
        chores = chore_db.list_all(active_only=True)
        assert len(chores) == 1
        assert chores[0].name == "B"

    def test_list_all_including_inactive(self, chore_db):
        c = chore_db.add_chore(name="A", frequency_days=1, assigned_to="X")
        chore_db.add_chore(name="B", frequency_days=2, assigned_to="Y")
        chore_db.delete_chore(c.id)
        chores = chore_db.list_all(active_only=False)
        assert len(chores) == 2


class TestChoreDBGetAndUpdate:
    def test_get_chore_exists(self, chore_db):
        added = chore_db.add_chore(name="Test", frequency_days=1, assigned_to="Me")
        fetched = chore_db.get_chore(added.id)
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_chore_not_found(self, chore_db):
        assert chore_db.get_chore(999) is None

    def test_set_calendar_event_id(self, chore_db):
        chore = chore_db.add_chore(name="Test", frequency_days=1, assigned_to="Me")
        assert chore_db.get_chore(chore.id).calendar_event_id is None
        chore_db.set_calendar_event_id(chore.id, "gcal_xyz")
        fetched = chore_db.get_chore(chore.id)
        assert fetched.calendar_event_id == "gcal_xyz"

    def test_mark_done_updates_next_due(self, chore_db):
        chore = chore_db.add_chore(
            name="Weekly", frequency_days=7, assigned_to="Me",
        )
        done = chore_db.mark_done(chore.id)
        assert done.last_done == date.today().isoformat()
        expected_next = (date.today() + timedelta(days=7)).isoformat()
        assert done.next_due == expected_next

    def test_mark_done_nonexistent_raises(self, chore_db):
        with pytest.raises(ValueError):
            chore_db.mark_done(999)


class TestChoreDBDueDateFiltering:
    def test_get_due_chores_filters_by_date(self, chore_db):
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=30)).isoformat()
        chore_db.add_chore(name="Due today", frequency_days=1, assigned_to="X", start_date=today)
        chore_db.add_chore(name="Future", frequency_days=1, assigned_to="Y", start_date=future)
        due = chore_db.get_due_chores(target_date=today)
        assert len(due) == 1
        assert due[0].name == "Due today"


class TestChoreDBDelete:
    def test_delete_chore_soft_deletes(self, chore_db):
        chore = chore_db.add_chore(name="ToDelete", frequency_days=1, assigned_to="X")
        assert chore_db.delete_chore(chore.id) is True
        assert chore_db.list_all(active_only=True) == []
        # Still exists in DB as inactive
        all_chores = chore_db.list_all(active_only=False)
        assert len(all_chores) == 1
        assert all_chores[0].active is False

    def test_delete_nonexistent_returns_false(self, chore_db):
        assert chore_db.delete_chore(999) is False

    def test_delete_already_deleted_returns_false(self, chore_db):
        chore = chore_db.add_chore(name="ToDelete", frequency_days=1, assigned_to="X")
        chore_db.delete_chore(chore.id)
        assert chore_db.delete_chore(chore.id) is False


class TestChoreDBUserScoping:
    def test_add_chore_with_user_id(self, chore_db):
        chore = chore_db.add_chore(
            name="Trash", frequency_days=7, assigned_to="Amit", user_id=12345,
        )
        assert chore.user_id == 12345

    def test_list_all_filters_by_user_id(self, chore_db):
        chore_db.add_chore(name="A", frequency_days=1, assigned_to="X", user_id=111)
        chore_db.add_chore(name="B", frequency_days=2, assigned_to="Y", user_id=222)
        chore_db.add_chore(name="C", frequency_days=3, assigned_to="Z", user_id=111)
        chores = chore_db.list_all(user_id=111)
        assert len(chores) == 2
        assert {c.name for c in chores} == {"A", "C"}

    def test_list_all_no_user_id_returns_all(self, chore_db):
        chore_db.add_chore(name="A", frequency_days=1, assigned_to="X", user_id=111)
        chore_db.add_chore(name="B", frequency_days=2, assigned_to="Y", user_id=222)
        chores = chore_db.list_all()
        assert len(chores) == 2

    def test_get_due_chores_filters_by_user_id(self, chore_db):
        from datetime import date
        today = date.today().isoformat()
        chore_db.add_chore(name="Mine", frequency_days=1, assigned_to="X",
                           user_id=111, start_date=today)
        chore_db.add_chore(name="Theirs", frequency_days=1, assigned_to="Y",
                           user_id=222, start_date=today)
        due = chore_db.get_due_chores(target_date=today, user_id=111)
        assert len(due) == 1
        assert due[0].name == "Mine"


class TestContactDBUserScoping:
    def test_add_contact_with_user_id(self, contact_db):
        contact = contact_db.add_contact("Yahav", "yahav@gmail.com", user_id=111)
        assert contact.user_id == 111

    def test_find_by_name_filters_by_user_id(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com", user_id=111)
        contact_db.add_contact("Yahav", "yahav2@gmail.com", user_id=222)
        found = contact_db.find_by_name("Yahav", user_id=111)
        assert found is not None
        assert found.email == "yahav@gmail.com"

    def test_find_by_name_no_user_id(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com", user_id=111)
        found = contact_db.find_by_name("Yahav")
        assert found is not None

    def test_list_all_filters_by_user_id(self, contact_db):
        contact_db.add_contact("A", "a@x.com", user_id=111)
        contact_db.add_contact("B", "b@x.com", user_id=222)
        contacts = contact_db.list_all(user_id=111)
        assert len(contacts) == 1
        assert contacts[0].name == "A"


class TestChoreDBMigration:
    def test_migration_adds_columns_to_old_schema(self, tmp_db_path):
        """Simulate an old DB without the new columns, verify migration works."""
        import sqlite3

        # Create a table with the old schema
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("""
            CREATE TABLE chores (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL,
                frequency_days INTEGER NOT NULL,
                last_done      TEXT,
                next_due       TEXT NOT NULL,
                assigned_to    TEXT NOT NULL,
                active         INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute(
            "INSERT INTO chores (name, frequency_days, next_due, assigned_to) VALUES (?, ?, ?, ?)",
            ("Old chore", 7, "2026-01-01", "Amit"),
        )
        conn.commit()
        conn.close()

        # Now init ChoreDB — should migrate
        db = ChoreDB(db_path=tmp_db_path)
        chores = db.list_all()
        assert len(chores) == 1
        assert chores[0].name == "Old chore"
        # New columns should have defaults
        assert chores[0].duration_minutes == 30
        assert chores[0].preferred_time_start == "09:00"
        assert chores[0].preferred_time_end == "21:00"
        assert chores[0].calendar_event_id is None
