"""Google Maps API integration — location enrichment and travel time.

Uses the Places API (New) Text Search endpoint to validate and enrich
raw location strings with formatted addresses and Google Maps URLs.

Uses the Distance Matrix API to calculate travel time between two
addresses for alarm time recommendations.

Gracefully degrades: returns None on any failure (no API key, timeout,
invalid response, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_TIMEOUT_SECONDS = 5


@dataclass
class EnrichedLocation:
    """Result of a successful Places API lookup."""

    display_name: str
    formatted_address: str
    maps_url: str


async def enrich_location(
    raw_location: str,
    api_key: str,
) -> EnrichedLocation | None:
    """Look up a raw location string via Google Maps Places API.

    Returns an EnrichedLocation with display name, formatted address,
    and a Google Maps URL — or None on any failure.
    """
    if not raw_location or not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _PLACES_TEXT_SEARCH_URL,
                json={"textQuery": raw_location},
                headers={
                    "X-Goog-Api-Key": api_key,
                    "X-Goog-FieldMask": "places.displayName,places.formattedAddress",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        places = data.get("places", [])
        if not places:
            logger.info("No Places results for '%s'", raw_location)
            return None

        place = places[0]
        display_name = place.get("displayName", {}).get("text", raw_location)
        formatted_address = place.get("formattedAddress", "")
        maps_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(formatted_address or display_name)}"

        return EnrichedLocation(
            display_name=display_name,
            formatted_address=formatted_address,
            maps_url=maps_url,
        )
    except Exception as exc:
        logger.warning("Google Maps enrichment failed for '%s': %s", raw_location, exc)
        return None


# ---------------------------------------------------------------------------
# Distance Matrix API — travel time calculation
# ---------------------------------------------------------------------------

_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


@dataclass
class TravelTimeResult:
    """Result of a successful Distance Matrix API lookup."""

    duration_minutes: int
    duration_text: str
    distance_text: str


async def get_travel_time(
    origin: str,
    destination: str,
    api_key: str,
) -> TravelTimeResult | None:
    """Calculate travel time between two addresses via Distance Matrix API.

    Returns a TravelTimeResult with duration and distance — or None on any failure.
    """
    if not origin or not destination or not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                _DISTANCE_MATRIX_URL,
                params={
                    "origins": origin,
                    "destinations": destination,
                    "key": api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "OK":
            logger.warning("Distance Matrix API status: %s", data.get("status"))
            return None

        rows = data.get("rows", [])
        if not rows:
            return None

        element = rows[0].get("elements", [{}])[0]
        if element.get("status") != "OK":
            logger.info(
                "Distance Matrix element status: %s for %s → %s",
                element.get("status"), origin, destination,
            )
            return None

        duration_seconds = element["duration"]["value"]
        duration_minutes = (duration_seconds + 59) // 60  # round up

        return TravelTimeResult(
            duration_minutes=duration_minutes,
            duration_text=element["duration"]["text"],
            distance_text=element["distance"]["text"],
        )
    except Exception as exc:
        logger.warning(
            "Distance Matrix API failed for '%s' → '%s': %s",
            origin, destination, exc,
        )
        return None
