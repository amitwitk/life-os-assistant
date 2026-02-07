"""Tests for the calendar adapter factory."""

import pytest
from unittest.mock import patch

from src.adapters.calendar_factory import create_calendar_adapter


class TestCreateCalendarAdapter:
    @patch("src.adapters.calendar_factory.settings")
    def test_returns_google_adapter(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "google"
        adapter = create_calendar_adapter()
        from src.adapters.google_calendar import GoogleCalendarAdapter
        assert isinstance(adapter, GoogleCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_returns_outlook_adapter(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "outlook"
        adapter = create_calendar_adapter()
        from src.adapters.outlook_calendar import OutlookCalendarAdapter
        assert isinstance(adapter, OutlookCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_returns_caldav_adapter(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "caldav"
        adapter = create_calendar_adapter()
        from src.adapters.caldav_calendar import CalDAVCalendarAdapter
        assert isinstance(adapter, CalDAVCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_case_insensitive(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "Google"
        adapter = create_calendar_adapter()
        from src.adapters.google_calendar import GoogleCalendarAdapter
        assert isinstance(adapter, GoogleCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_unknown_provider_raises(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "nonexistent"
        with pytest.raises(ValueError, match="Unknown CALENDAR_PROVIDER"):
            create_calendar_adapter()


class TestCalendarAdapterTokenPassthrough:
    @patch("src.adapters.calendar_factory.settings")
    def test_google_receives_token_json(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "google"
        adapter = create_calendar_adapter(token_json='{"token": "abc"}')
        assert adapter._token_json == '{"token": "abc"}'

    @patch("src.adapters.calendar_factory.settings")
    def test_outlook_receives_token_json(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "outlook"
        adapter = create_calendar_adapter(token_json='{"token": "abc"}')
        assert adapter._token_json == '{"token": "abc"}'

    @patch("src.adapters.calendar_factory.settings")
    def test_caldav_receives_cred_json(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "caldav"
        adapter = create_calendar_adapter(token_json='{"url": "x"}')
        assert adapter._cred_json == '{"url": "x"}'

    @patch("src.adapters.calendar_factory.settings")
    def test_google_no_token_defaults_to_none(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "google"
        adapter = create_calendar_adapter()
        assert adapter._token_json is None
