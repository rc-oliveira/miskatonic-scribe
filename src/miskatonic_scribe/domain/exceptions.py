"""Custom domain and infrastructure exceptions."""

from __future__ import annotations


class MiskatonicScribeError(Exception):
    """Base exception for Miskatonic Scribe errors."""


class AudioStorageError(MiskatonicScribeError):
    """Raised when downloading, saving, or cleaning audio files fails."""


class TranscriptionError(MiskatonicScribeError):
    """Raised when Whisper fails to transcribe an audio file or returns empty text."""


class LLMExtractionError(MiskatonicScribeError):
    """Raised when all Gemini models (primary + fallbacks) fail or return invalid JSON."""


class CardDatabaseError(MiskatonicScribeError):
    """Raised when downloading or parsing cards.json from ArkhamDB fails."""


class PersistenceError(MiskatonicScribeError):
    """Raised when saving or loading user card collections fails."""
