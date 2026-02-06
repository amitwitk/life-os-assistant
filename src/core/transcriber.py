"""
LifeOS Assistant — Audio Transcriber.

Voice is the fastest capture method — speaking is faster than typing.
After transcription, text flows into the same Claude parser as typed messages.

IMPORTANT: This is the ONLY module in the entire project that uses the OpenAI SDK.
OpenAI is used here exclusively for Whisper audio transcription.
All other LLM calls (parsing, summarization) use the Anthropic SDK.
"""

from __future__ import annotations

import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper.

    Args:
        file_path: Path to the audio file (OGG, MP3, etc.).

    Returns:
        Transcribed text string.

    Raises:
        Exception: If the Whisper API call fails.
    """
    try:
        with open(file_path, "rb") as audio_file:
            response = await _client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="he",  # Hebrew hint — Whisper handles mixed Hebrew/English well
            )
        text = response.text.strip()
        logger.info("Transcribed %d chars from %s", len(text), Path(file_path).name)
        return text
    except Exception as exc:
        logger.error("Whisper transcription failed for %s: %s", file_path, exc)
        raise
