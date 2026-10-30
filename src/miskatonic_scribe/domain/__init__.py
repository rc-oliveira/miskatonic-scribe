"""Domain package exports."""

from miskatonic_scribe.domain.exceptions import (
    AudioStorageError,
    CardDatabaseError,
    LLMExtractionError,
    MiskatonicScribeError,
    PersistenceError,
    TranscriptionError,
)
from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardAlternative,
    CardMatchResult,
    ExtractedCardIntent,
    ExtractedDeckPayload,
    InvestigatorMatchResult,
    PipelineExecutionResult,
)

__all__ = [
    "ArkhamCardRecord",
    "AudioStorageError",
    "CardAlternative",
    "CardDatabaseError",
    "CardMatchResult",
    "ExtractedCardIntent",
    "ExtractedDeckPayload",
    "InvestigatorMatchResult",
    "LLMExtractionError",
    "MiskatonicScribeError",
    "PersistenceError",
    "PipelineExecutionResult",
    "TranscriptionError",
]
