"""
LifeOS Assistant — LLM Provider Abstraction.

Single public function `complete()` that routes to the configured provider.
Provider is selected at startup via the LLM_PROVIDER env var.
Supports: gemini (default), anthropic, openai, cohere.
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Type alias for provider implementations
_ProviderFn = Callable[[str, str, str, int], Awaitable[str]]

# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def _complete_gemini(api_key: str, model: str, system: str, user_message: str, max_tokens: int) -> str:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    gm = genai.GenerativeModel(
        model_name=model,
        system_instruction=system,
    )
    response = await gm.generate_content_async(
        user_message,
        generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens),
    )
    return response.text


async def _complete_anthropic(api_key: str, model: str, system: str, user_message: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


async def _complete_openai(api_key: str, model: str, system: str, user_message: str, max_tokens: int) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content


async def _complete_cohere(api_key: str, model: str, system: str, user_message: str, max_tokens: int) -> str:
    import cohere

    client = cohere.AsyncClientV2(api_key=api_key)
    response = await client.chat(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )
    return response.message.content[0].text


# ---------------------------------------------------------------------------
# Provider selection (runs once at first call)
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, tuple[_ProviderFn, str]] = {
    "gemini":    (_complete_gemini,    "gemini-2.0-flash"),
    "anthropic": (_complete_anthropic, "claude-haiku-4-5-20251001"),
    "openai":    (_complete_openai,    "gpt-4o-mini"),
    "cohere":    (_complete_cohere,   "command-a-03-2025"),
}


def _select_provider() -> tuple[_ProviderFn, str, str]:
    """Read env vars and return (provider_fn, model, api_key)."""
    from src.config import settings

    provider_name = settings.LLM_PROVIDER.lower()
    if provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown LLM_PROVIDER={provider_name!r}. "
            f"Supported: {', '.join(_PROVIDERS)}"
        )

    fn, default_model = _PROVIDERS[provider_name]
    model = settings.LLM_MODEL or default_model
    api_key = settings.LLM_API_KEY

    logger.info("LLM provider: %s, model: %s", provider_name, model)
    return fn, model, api_key


# Lazy singleton — populated on first call to complete()
_provider_fn: _ProviderFn | None = None
_model: str = ""
_api_key: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def complete(system: str, user_message: str, max_tokens: int = 256) -> str:
    """Send a prompt to the configured LLM provider and return the response text.

    Raises on API errors — callers should handle exceptions.
    """
    global _provider_fn, _model, _api_key

    if _provider_fn is None:
        _provider_fn, _model, _api_key = _select_provider()

    return await _provider_fn(_api_key, _model, system, user_message, max_tokens)
