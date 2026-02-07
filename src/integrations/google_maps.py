"""Google Maps Places API integration — location enrichment.

Uses the Places API (New) Text Search endpoint to validate and enrich
raw location strings with formatted addresses and Google Maps URLs.

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
