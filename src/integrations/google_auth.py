"""
LifeOS Assistant — Google Calendar Authentication.

Enables Google Calendar access, which is critical for two pillars:
the Capture System (writing events) and the Morning Briefing (reading today's schedule).
Without auth, neither pillar works.
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Authenticate and return a Google Calendar API v3 service object.

    Flow:
    1. Try loading existing token from disk.
    2. If expired, refresh with the refresh token.
    3. If no valid credentials, run the interactive OAuth2 consent flow.
    4. Persist the (refreshed) token for next time.
    """
    from src.config import settings

    token_path = Path(settings.GOOGLE_TOKEN_PATH)
    creds_path = Path(settings.GOOGLE_CREDENTIALS_PATH)
    creds: Credentials | None = None

    # 1. Load existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        logger.debug("Loaded existing token from %s", token_path)

    # 2. Refresh or re-authenticate
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info("Token refreshed successfully")
        except Exception as exc:
            logger.warning("Token refresh failed (%s), re-authenticating", exc)
            creds = None

    if not creds or not creds.valid:
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials file not found at {creds_path}. "
                "Download it from the Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(port=0)
        logger.info("New credentials obtained via OAuth2 consent flow")

    # 3. Save token
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    logger.debug("Token saved to %s", token_path)

    # 4. Build and return service
    service = build("calendar", "v3", credentials=creds)
    logger.info("Google Calendar service built successfully")
    return service


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running Google Calendar authorization flow...")
    svc = get_calendar_service()
    # Quick sanity check: list next 3 events
    events = svc.events().list(calendarId="primary", maxResults=3).execute()
    items = events.get("items", [])
    print(f"Auth successful! Found {len(items)} upcoming event(s).")
    for item in items:
        print(f"  - {item.get('summary', '(no title)')}")
