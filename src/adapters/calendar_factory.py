"""Calendar adapter factory — creates the right adapter based on config."""

from __future__ import annotations

from src.config import settings
from src.ports.calendar_port import CalendarPort


def create_calendar_adapter(token_json: str | None = None) -> CalendarPort:
    """Return the calendar adapter matching CALENDAR_PROVIDER setting.

    Args:
        token_json: Per-user credentials. Passed to adapter constructors.
    """
    provider = settings.CALENDAR_PROVIDER.lower()

    if provider == "google":
        from src.adapters.google_calendar import GoogleCalendarAdapter

        return GoogleCalendarAdapter(token_json=token_json)

    if provider == "outlook":
        from src.adapters.outlook_calendar import OutlookCalendarAdapter

        return OutlookCalendarAdapter(token_json=token_json)

    if provider == "caldav":
        from src.adapters.caldav_calendar import CalDAVCalendarAdapter

        return CalDAVCalendarAdapter(cred_json=token_json)

    raise ValueError(f"Unknown CALENDAR_PROVIDER: {provider!r}")
