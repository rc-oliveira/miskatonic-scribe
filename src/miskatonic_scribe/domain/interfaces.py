"""Domain interfaces (Protocols/ABCs) enforcing Dependency Inversion (SOLID)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardMatchResult,
    ExtractedCardIntent,
    ExtractedDeckPayload,
    InvestigatorMatchResult,
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
    """Interface for LLM-based card and investigator extraction from text or audio."""

    @abstractmethod
    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        """Extract cards from text and return (list of ExtractedCardIntent, model_name_used)."""

    def extract_deck_payload(
        self,
        transcribed_text: Optional[str] = None,
        audio_path: Optional[Path] = None,
    ) -> Tuple[ExtractedDeckPayload, str]:
        """Extract full deck payload (investigator, cards, removals) from text and/or audio."""
        if not transcribed_text:
            raise NotImplementedError("Subclasses must implement extract_deck_payload for audio.")
        intents, model_used = self.extract_cards(transcribed_text)
        return (
            ExtractedDeckPayload(transcricao=transcribed_text, cartas=intents),
            model_used,
        )


class ICardRepository(ABC):
    """Interface for loading and querying ArkhamDB cards."""

    @abstractmethod
    def load_cards(self) -> Sequence[ArkhamCardRecord]:
        """Load all player cards from local cache or download from ArkhamDB."""

    @abstractmethod
    def refresh_cards(self) -> Sequence[ArkhamCardRecord]:
        """Force download of cards.json from ArkhamDB and reload the in-memory cache."""


class ICardMatcher(ABC):
    """Interface for fuzzy-matching LLM intents and investigators against ArkhamDB card records."""

    @abstractmethod
    def match_intent(
        self,
        intent: ExtractedCardIntent,
        investigator: Optional[InvestigatorMatchResult] = None,
    ) -> CardMatchResult:
        """Match a single extracted card intent against ArkhamDB."""

    @abstractmethod
    def match_all(
        self,
        intents: Sequence[ExtractedCardIntent],
        investigator: Optional[InvestigatorMatchResult] = None,
    ) -> List[CardMatchResult]:
        """Match a sequence of extracted card intents against ArkhamDB."""

    def match_investigator(self, investigator_name: str) -> InvestigatorMatchResult:
        """Match an investigator name against ArkhamDB investigator records."""
        return InvestigatorMatchResult(raw_name=investigator_name, matched=False)


class IUserCollectionRepository(ABC):
    """Interface for persisting confirmed card registrations per Telegram user."""

    @abstractmethod
    def register_cards(
        self,
        user_id: int,
        username: str | None,
        matches: Sequence[CardMatchResult],
        investigator: Optional[InvestigatorMatchResult] = None,
    ) -> int:
        """Persist confirmed card matches for a user and return total copy count stored."""

    @abstractmethod
    def get_user_collection(self, user_id: int) -> List[dict]:
        """Return all registered card entries for a user."""
