"""Tests for src.integrations.google_maps — Places API location enrichment."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.integrations.google_maps import EnrichedLocation, enrich_location


class TestEnrichLocation:
    @pytest.mark.asyncio
    async def test_successful_lookup(self):
        """Successful API response returns EnrichedLocation."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "places": [
                {
                    "displayName": {"text": "Blue Bottle Coffee"},
                    "formattedAddress": "315 Linden St, San Francisco, CA 94102",
                }
            ]
        }

        with patch("src.integrations.google_maps.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await enrich_location("Blue Bottle Coffee", "fake-api-key")

        assert result is not None
        assert isinstance(result, EnrichedLocation)
        assert result.display_name == "Blue Bottle Coffee"
        assert result.formatted_address == "315 Linden St, San Francisco, CA 94102"
        assert "google.com/maps" in result.maps_url

    @pytest.mark.asyncio
    async def test_no_results_returns_none(self):
        """API returns empty places list → None."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"places": []}

        with patch("src.integrations.google_maps.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await enrich_location("Nonexistent Place XYZ", "fake-api-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """HTTP error from API → None (graceful degradation)."""
        with patch("src.integrations.google_maps.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("API Error"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await enrich_location("Some Place", "fake-api-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """Timeout from API → None (graceful degradation)."""
        import httpx

        with patch("src.integrations.google_maps.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await enrich_location("Some Place", "fake-api-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_location_returns_none(self):
        """Empty location string → None without calling API."""
        result = await enrich_location("", "fake-api-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_none(self):
        """Empty API key → None without calling API."""
        result = await enrich_location("Some Place", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_display_name_without_address(self):
        """Place with display name but no formatted address."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "places": [
                {
                    "displayName": {"text": "The Office"},
                    "formattedAddress": "",
                }
            ]
        }

        with patch("src.integrations.google_maps.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await enrich_location("The Office", "fake-api-key")

        assert result is not None
        assert result.display_name == "The Office"
        assert result.formatted_address == ""
