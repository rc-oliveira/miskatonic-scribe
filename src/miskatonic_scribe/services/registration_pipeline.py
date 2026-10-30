"""End-to-end orchestration pipeline for voice card registration."""

from __future__ import annotations

import logging
from pathlib import Path

from miskatonic_scribe.domain.interfaces import (
    IAudioTranscriber,
    ICardIntentExtractor,
    ICardMatcher,
)
from miskatonic_scribe.domain.models import PipelineExecutionResult
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher

logger = logging.getLogger(__name__)


class CardRegistrationPipeline:
    """Executes the strict pipeline: Transcription -> LLM Structuring -> Fuzzy Matching."""

    def __init__(
        self,
        transcriber: IAudioTranscriber,
        extractor: ICardIntentExtractor,
        matcher: ICardMatcher,
    ) -> None:
        self._transcriber = transcriber
        self._extractor = extractor
        self._matcher = matcher

    def process_audio(self, audio_path: Path) -> PipelineExecutionResult:
        """Process a downloaded local audio file through Whisper, Gemini, and ArkhamDB matcher."""
        logger.info("Starting registration pipeline for audio '%s'", audio_path)

        # Step 1: Transcription via local Whisper
        transcribed_text = self._transcriber.transcribe(audio_path)

        # Step 2: Structured extraction via Gemini (with fallback models)
        extracted_intents, model_used = self._extractor.extract_cards(transcribed_text)

        # Step 3: Fuzzy matching against ArkhamDB cards.json
        if isinstance(self._matcher, ArkhamFuzzyCardMatcher):
            match_results = self._matcher.match_all(extracted_intents, model_used=model_used)
        else:
            match_results = self._matcher.match_all(extracted_intents)

        logger.info(
            "Pipeline completed for '%s': %d card(s) processed via model '%s'.",
            audio_path.name,
            len(match_results),
            model_used,
        )

        return PipelineExecutionResult(
            transcribed_text=transcribed_text,
            model_used=model_used,
            extracted_intents=extracted_intents,
            match_results=match_results,
        )
