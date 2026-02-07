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


def get_google_auth_url() -> tuple[str, InstalledAppFlow]:
    """Generate a Google OAuth2 authorization URL for manual flow.

    Returns (auth_url, flow) — the user visits auth_url, authorizes,
    and pastes back the auth code.
    """
    from src.config import settings

    creds_path = Path(settings.GOOGLE_CREDENTIALS_PATH)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google credentials file not found at {creds_path}. "
            "Download it from the Google Cloud Console."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path), SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url, flow


def exchange_google_auth_code(flow: InstalledAppFlow, code: str) -> str:
    """Exchange an authorization code for credentials.

    Returns the token as a JSON string suitable for storing in UserDB.
    """
    flow.fetch_token(code=code)
    return flow.credentials.to_json()


def get_calendar_service_for_user(token_json: str):
    """Build a Google Calendar API service from stored user credentials.

    Refreshes the token if expired.
    """
    import json

    creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds)


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
