"""Tests for src.integrations.google_maps — Places API enrichment."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.integrations.google_maps import enrich_location, EnrichedLocation


class TestEnrichLocation:
    @pytest.mark.asyncio
    async def test_successful_enrichment(self):
        """A successful Places API response returns EnrichedLocation."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "places": [
                {
                    "displayName": {"text": "Blue Bottle Coffee"},
                    "formattedAddress": "1 Ferry Building, San Francisco, CA",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            result = await enrich_location("Blue Bottle Coffee", "fake-key")

        assert result is not None
        assert result.display_name == "Blue Bottle Coffee"
        assert result.formatted_address == "1 Ferry Building, San Francisco, CA"
        assert "google.com/maps" in result.maps_url

    @pytest.mark.asyncio
    async def test_no_places_returns_none(self):
        """Empty places array returns None."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            result = await enrich_location("nonexistent place xyz", "fake-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_location_returns_none(self):
        """Empty location string returns None without making API call."""
        result = await enrich_location("", "fake-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_none(self):
        """Empty API key returns None without making API call."""
        result = await enrich_location("Some Place", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """HTTP error gracefully returns None."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("Connection timeout"))

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            result = await enrich_location("Some Place", "fake-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_formatted_address(self):
        """Place without formattedAddress still works — uses display name for URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "places": [
                {
                    "displayName": {"text": "The Office"},
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            result = await enrich_location("The Office", "fake-key")

        assert result is not None
        assert result.display_name == "The Office"
        assert result.formatted_address == ""
        assert "The+Office" in result.maps_url

    @pytest.mark.asyncio
    async def test_maps_url_uses_formatted_address(self):
        """Maps URL uses formatted address when available."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "places": [
                {
                    "displayName": {"text": "Cafe"},
                    "formattedAddress": "123 Main St, Tel Aviv",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            result = await enrich_location("Cafe", "fake-key")

        assert "123+Main+St" in result.maps_url

    @pytest.mark.asyncio
    async def test_sends_correct_headers(self):
        """Verifies the correct API key header and field mask are sent."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.integrations.google_maps.httpx.AsyncClient", return_value=mock_client):
            await enrich_location("Test", "my-api-key")

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Goog-Api-Key"] == "my-api-key"
        assert "displayName" in headers["X-Goog-FieldMask"]
