"""Tests for src.data.db — UserDB (multi-user support)."""

import pytest

from src.data.db import UserDB


class TestUserDBAddAndGet:
    def test_add_user_and_get(self, user_db):
        user = user_db.add_user(
            telegram_user_id=12345,
            display_name="Amit",
            invited_by=None,
            is_admin=True,
        )
        assert user.telegram_user_id == 12345
        assert user.display_name == "Amit"
        assert user.is_admin is True
        assert user.onboarded is False
        assert user.calendar_token_json is None
        assert user.created_at != ""

        fetched = user_db.get_user(12345)
        assert fetched is not None
        assert fetched.telegram_user_id == 12345
        assert fetched.display_name == "Amit"

    def test_get_user_not_found(self, user_db):
        assert user_db.get_user(99999) is None

    def test_duplicate_user_raises(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        with pytest.raises(Exception):
            user_db.add_user(12345, "Amit Again", is_admin=False)


class TestUserDBRegistration:
    def test_is_registered_true(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        assert user_db.is_registered(12345) is True

    def test_is_registered_false(self, user_db):
        assert user_db.is_registered(99999) is False


class TestUserDBTokenAndOnboarding:
    def test_set_calendar_token(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.set_calendar_token(12345, '{"token": "abc123"}')
        user = user_db.get_user(12345)
        assert user.calendar_token_json == '{"token": "abc123"}'

    def test_mark_onboarded(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        assert user_db.get_user(12345).onboarded is False
        user_db.mark_onboarded(12345)
        assert user_db.get_user(12345).onboarded is True


class TestUserDBListUsers:
    def test_list_users_empty(self, user_db):
        assert user_db.list_users() == []

    def test_list_users_returns_all(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.add_user(67890, "Dana", invited_by=12345, is_admin=False)
        users = user_db.list_users()
        assert len(users) == 2
        names = {u.display_name for u in users}
        assert names == {"Amit", "Dana"}

    def test_invited_by_stored(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.add_user(67890, "Dana", invited_by=12345, is_admin=False)
        dana = user_db.get_user(67890)
        assert dana.invited_by == 12345


class TestUserDBHomeAddress:
    def test_home_address_default_none(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user = user_db.get_user(12345)
        assert user.home_address is None

    def test_set_home_address(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.set_home_address(12345, "123 Main St, Tel Aviv")
        user = user_db.get_user(12345)
        assert user.home_address == "123 Main St, Tel Aviv"

    def test_update_home_address(self, user_db):
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.set_home_address(12345, "Old Address")
        user_db.set_home_address(12345, "New Address")
        user = user_db.get_user(12345)
        assert user.home_address == "New Address"


class TestUserDBBackfill:
    def test_backfill_user_id(self, tmp_path):
        """Backfill assigns orphan chores/contacts to a user."""
        from src.data.db import ChoreDB, ContactDB

        db_path = str(tmp_path / "test_backfill.db")
        chore_db = ChoreDB(db_path=db_path)
        contact_db = ContactDB(db_path=db_path)
        user_db = UserDB(db_path=db_path)

        # Add data without user_id
        chore_db.add_chore("Test chore", frequency_days=7, assigned_to="Me")
        contact_db.add_contact("Yahav", "yahav@gmail.com")

        # Backfill
        user_db.add_user(12345, "Amit", is_admin=True)
        user_db.backfill_user_id(12345)

        # Verify chores are now scoped
        chores = chore_db.list_all(user_id=12345)
        assert len(chores) == 1
        assert chores[0].name == "Test chore"

        # Verify contacts are now scoped
        contact = contact_db.find_by_name("Yahav", user_id=12345)
        assert contact is not None
        assert contact.email == "yahav@gmail.com"
