"""Microsoft Graph authentication helper.

Uses azure-identity ClientSecretCredential for app-only auth.
The returned GraphServiceClient is used by OutlookCalendarAdapter.
"""

from __future__ import annotations

import logging

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient

from src.config import settings

logger = logging.getLogger(__name__)

_client: GraphServiceClient | None = None


def get_graph_client() -> GraphServiceClient:
    """Return a cached, authenticated GraphServiceClient."""
    global _client
    if _client is not None:
        return _client

    credential = ClientSecretCredential(
        tenant_id=settings.MS_TENANT_ID,
        client_id=settings.MS_CLIENT_ID,
        client_secret=settings.MS_CLIENT_SECRET,
    )
    _client = GraphServiceClient(
        credentials=credential,
        scopes=["https://graph.microsoft.com/.default"],
    )
    logger.info("Microsoft Graph client initialized (tenant=%s)", settings.MS_TENANT_ID)
    return _client
