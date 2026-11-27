"""Voice note transcription service.

Downloads audio from Twilio, transcribes with Deepgram (primary) or
OpenAI Whisper (fallback). Returns plain text for the intent classifier.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/ogg") -> Optional[str]:
    """
    Transcribe audio bytes to text.

    Tries Deepgram first (better Nigerian accent support), falls back to Whisper.
    Returns transcribed text, or None if both fail.
    """
    text = await _transcribe_deepgram(audio_bytes, content_type)
    if text:
        return text

    text = await _transcribe_whisper(audio_bytes)
    return text


async def _transcribe_deepgram(audio_bytes: bytes, content_type: str) -> Optional[str]:
    """Transcribe via Deepgram Nova-2."""
    try:
        import httpx
        from app.core.config import settings

        if not settings.DEEPGRAM_API_KEY:
            return None

        # Deepgram supports ogg/opus natively (WhatsApp voice format)
        url = (
            "https://api.deepgram.com/v1/listen"
            "?model=nova-2"
            "&language=en-NG"       # Nigerian English
            "&smart_format=true"
            "&punctuate=true"
        )
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, content=audio_bytes, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            transcript = (
                data.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
            )
            return transcript.strip() or None
    except Exception as exc:
        logger.warning(f"Deepgram transcription failed: {exc}")
        return None


async def _transcribe_whisper(audio_bytes: bytes) -> Optional[str]:
    """Transcribe via OpenAI Whisper."""
    try:
        import openai
        import io
        from app.core.config import settings

        if not settings.OPENAI_API_KEY:
            return None

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        # Whisper needs a file-like with a name so it knows the codec
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"

        resp = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        )
        return resp.text.strip() or None
    except Exception as exc:
        logger.warning(f"Whisper transcription failed: {exc}")
        return None
