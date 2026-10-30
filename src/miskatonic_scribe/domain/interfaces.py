"""Domain interfaces (Protocols/ABCs) enforcing Dependency Inversion (SOLID)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Sequence, Tuple

from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardMatchResult,
    ExtractedCardIntent,
)


class IAudioStorageManager(ABC):
    """Interface for managing local audio files and executing cleanup routines."""

    @abstractmethod
    def prepare_audio_path(self, file_unique_id: str, extension: str = ".ogg") -> Path:
        """Generate target path for a new audio file and trigger maintenance cleanup."""

    @abstractmethod
    def run_maintenance(self) -> List[Path]:
        """Remove audio files older than X days or exceeding max_files threshold."""


class IAudioTranscriber(ABC):
    """Interface for speech-to-text transcription."""

    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        """Transcribe a local audio file into text."""


class ICardIntentExtractor(ABC):
    """Interface for LLM-based card extraction from noisy transcriptions."""

    @abstractmethod
    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        """Extract cards from text and return (list of ExtractedCardIntent, model_name_used)."""


class ICardRepository(ABC):
    """Interface for loading and querying ArkhamDB cards."""

    @abstractmethod
    def load_cards(self) -> Sequence[ArkhamCardRecord]:
        """Load all player cards from local cache or download from ArkhamDB."""


class ICardMatcher(ABC):
    """Interface for fuzzy-matching LLM intents against ArkhamDB card records."""

    @abstractmethod
    def match_intent(self, intent: ExtractedCardIntent) -> CardMatchResult:
        """Match a single extracted card intent against ArkhamDB."""

    @abstractmethod
    def match_all(self, intents: Sequence[ExtractedCardIntent]) -> List[CardMatchResult]:
        """Match a sequence of extracted card intents against ArkhamDB."""


class IUserCollectionRepository(ABC):
    """Interface for persisting confirmed card registrations per Telegram user."""

    @abstractmethod
    def register_cards(self, user_id: int, username: str | None, matches: Sequence[CardMatchResult]) -> int:
        """Persist confirmed card matches for a user and return total copy count stored."""

    @abstractmethod
    def get_user_collection(self, user_id: int) -> List[dict]:
        """Return all registered card entries for a user."""
