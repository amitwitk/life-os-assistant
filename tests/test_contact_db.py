"""Tests for ContactDB — CRUD operations on the contacts table."""

import pytest

from src.data.db import ContactDB
from src.data.models import Contact


class TestContactDBAdd:
    def test_add_contact_returns_contact(self, contact_db):
        contact = contact_db.add_contact("Yahav", "yahav@gmail.com")
        assert isinstance(contact, Contact)
        assert contact.name == "Yahav"
        assert contact.email == "yahav@gmail.com"
        assert contact.name_normalized == "yahav"
        assert contact.id >= 1

    def test_add_contact_strips_whitespace(self, contact_db):
        contact = contact_db.add_contact("  Dan  ", "  dan@example.com  ")
        assert contact.name == "Dan"
        assert contact.email == "dan@example.com"


class TestContactDBFindByName:
    def test_find_exact_match(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com")
        found = contact_db.find_by_name("Yahav")
        assert found is not None
        assert found.email == "yahav@gmail.com"

    def test_find_case_insensitive(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com")
        found = contact_db.find_by_name("yahav")
        assert found is not None
        assert found.email == "yahav@gmail.com"

    def test_find_case_insensitive_uppercase(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com")
        found = contact_db.find_by_name("YAHAV")
        assert found is not None

    def test_find_not_found_returns_none(self, contact_db):
        assert contact_db.find_by_name("Unknown") is None

    def test_find_strips_whitespace(self, contact_db):
        contact_db.add_contact("Dan", "dan@example.com")
        found = contact_db.find_by_name("  Dan  ")
        assert found is not None


class TestContactDBListAll:
    def test_list_empty(self, contact_db):
        assert contact_db.list_all() == []

    def test_list_returns_all(self, contact_db):
        contact_db.add_contact("Yahav", "yahav@gmail.com")
        contact_db.add_contact("Dan", "dan@example.com")
        contacts = contact_db.list_all()
        assert len(contacts) == 2
        names = {c.name for c in contacts}
        assert names == {"Yahav", "Dan"}


class TestContactDBDelete:
    def test_delete_existing(self, contact_db):
        contact = contact_db.add_contact("Yahav", "yahav@gmail.com")
        assert contact_db.delete_contact(contact.id) is True
        assert contact_db.find_by_name("Yahav") is None

    def test_delete_nonexistent(self, contact_db):
        assert contact_db.delete_contact(999) is False

    def test_delete_removes_from_list(self, contact_db):
        c1 = contact_db.add_contact("Yahav", "yahav@gmail.com")
        contact_db.add_contact("Dan", "dan@example.com")
        contact_db.delete_contact(c1.id)
        contacts = contact_db.list_all()
        assert len(contacts) == 1
        assert contacts[0].name == "Dan"
