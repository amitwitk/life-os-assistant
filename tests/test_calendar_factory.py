"""Tests for the calendar adapter factory."""

import pytest
from unittest.mock import patch

from src.adapters.calendar_factory import create_calendar_adapter


class TestCreateCalendarAdapter:
    @patch("src.adapters.calendar_factory.settings")
    def test_returns_google_adapter(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "google"
        with patch("src.adapters.google_calendar.get_calendar_service"):
            adapter = create_calendar_adapter()
        from src.adapters.google_calendar import GoogleCalendarAdapter
        assert isinstance(adapter, GoogleCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_returns_outlook_adapter(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "outlook"
        with patch("src.adapters.outlook_calendar.get_graph_client"):
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
        with patch("src.adapters.google_calendar.get_calendar_service"):
            adapter = create_calendar_adapter()
        from src.adapters.google_calendar import GoogleCalendarAdapter
        assert isinstance(adapter, GoogleCalendarAdapter)

    @patch("src.adapters.calendar_factory.settings")
    def test_unknown_provider_raises(self, mock_settings):
        mock_settings.CALENDAR_PROVIDER = "nonexistent"
        with pytest.raises(ValueError, match="Unknown CALENDAR_PROVIDER"):
            create_calendar_adapter()
