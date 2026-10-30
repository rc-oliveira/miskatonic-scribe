"""End-to-end orchestration pipeline for voice and text card registration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from thefuzz import fuzz

from miskatonic_scribe.domain.interfaces import (
    IAudioTranscriber,
    ICardIntentExtractor,
    ICardMatcher,
)
from miskatonic_scribe.domain.models import (
    CardMatchResult,
    ExtractedDeckPayload,
    InvestigatorMatchResult,
    PipelineExecutionResult,
)
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher

logger = logging.getLogger(__name__)


class CardRegistrationPipeline:
    """Executes the pipeline for audio (Whisper / Gemini Audio / Hybrid) and text messages."""

    def __init__(
        self,
        transcriber: IAudioTranscriber,
        extractor: ICardIntentExtractor,
        matcher: ICardMatcher,
        transcription_mode: str = "whisper",
    ) -> None:
        self._transcriber = transcriber
        self._extractor = extractor
        self._matcher = matcher
        self._transcription_mode = (transcription_mode or "whisper").strip().lower()

    def _build_execution_result(
        self,
        payload: ExtractedDeckPayload,
        model_used: str,
        fallback_text: str,
    ) -> PipelineExecutionResult:
        """Match investigator and cards from an ExtractedDeckPayload."""
        investigator_match: Optional[InvestigatorMatchResult] = None
        if payload.investigador:
            investigator_match = self._matcher.match_investigator(payload.investigador)

        if isinstance(self._matcher, ArkhamFuzzyCardMatcher):
            match_results = self._matcher.match_all(payload.cartas, model_used=model_used)
        else:
            match_results = self._matcher.match_all(payload.cartas)

        transcribed_text = (payload.transcricao or fallback_text or "").strip()
        return PipelineExecutionResult(
            transcribed_text=transcribed_text,
            model_used=model_used,
            extracted_intents=payload.cartas,
            match_results=match_results,
            investigator_match=investigator_match,
            removed_names=payload.remover,
        )

    def process_audio(self, audio_path: Path) -> PipelineExecutionResult:
        """Process a local audio file according to the configured transcription_mode."""
        logger.info(
            "Starting registration pipeline for audio '%s' (mode=%s)",
            audio_path.name,
            self._transcription_mode,
        )

        if self._transcription_mode == "gemini_audio":
            payload, model_used = self._extractor.extract_deck_payload(
                transcribed_text=None,
                audio_path=audio_path,
            )
            fallback_text = payload.transcricao or f"[Áudio processado diretamente via {model_used}]"
        elif self._transcription_mode == "hybrid":
            transcribed_text = self._transcriber.transcribe(audio_path)
            payload, model_used = self._extractor.extract_deck_payload(
                transcribed_text=transcribed_text,
                audio_path=audio_path,
            )
            fallback_text = transcribed_text
        else:
            # Default: "whisper"
            transcribed_text = self._transcriber.transcribe(audio_path)
            payload, model_used = self._extractor.extract_deck_payload(
                transcribed_text=transcribed_text,
                audio_path=None,
            )
            fallback_text = transcribed_text

        result = self._build_execution_result(payload, model_used, fallback_text)
        logger.info(
            "Audio pipeline completed for '%s': investigator=%r, %d card(s) via '%s'.",
            audio_path.name,
            result.investigator_match.official_name if result.investigator_match else None,
            len(result.match_results),
            model_used,
        )
        return result

    def process_text(self, user_text: str) -> PipelineExecutionResult:
        """Process a text message (new deck list or correction/addition to a pending batch)."""
        cleaned_text = user_text.strip()
        logger.info("Starting registration pipeline for text message (%d chars)", len(cleaned_text))
        payload, model_used = self._extractor.extract_deck_payload(
            transcribed_text=cleaned_text,
            audio_path=None,
        )
        result = self._build_execution_result(payload, model_used, cleaned_text)
        logger.info(
            "Text pipeline completed: investigator=%r, %d card(s), %d removal(s) via '%s'.",
            result.investigator_match.official_name if result.investigator_match else None,
            len(result.match_results),
            len(result.removed_names),
            model_used,
        )
        return result

    @staticmethod
    def merge_pending_result(
        current: PipelineExecutionResult,
        delta: PipelineExecutionResult,
    ) -> PipelineExecutionResult:
        """Apply a text correction/addition delta onto an existing pending PipelineExecutionResult."""
        remaining_matches: List[CardMatchResult] = []
        removals_lower = [r.lower().strip() for r in delta.removed_names if r.strip()]

        for item in current.match_results:
            candidate_names = [
                item.intent.nome.lower(),
                (item.official_name or "").lower(),
                (item.card_code or "").lower(),
            ]
            should_remove = False
            for rem in removals_lower:
                if any(
                    rem == cname or (cname and fuzz.ratio(rem, cname) >= 82)
                    for cname in candidate_names
                    if cname
                ):
                    should_remove = True
                    break
            if not should_remove:
                remaining_matches.append(item)

        # Merge or append new matches from delta
        for new_match in delta.match_results:
            replaced = False
            if new_match.matched and new_match.card_code:
                for idx, existing in enumerate(remaining_matches):
                    if existing.card_code == new_match.card_code:
                        remaining_matches[idx] = new_match
                        replaced = True
                        break
            if not replaced:
                remaining_matches.append(new_match)

        merged_investigator = delta.investigator_match or current.investigator_match
        merged_transcription = (
            f"{current.transcribed_text} | [Edição: {delta.transcribed_text}]"
            if current.transcribed_text
            else delta.transcribed_text
        )

        return PipelineExecutionResult(
            transcribed_text=merged_transcription,
            model_used=delta.model_used or current.model_used,
            extracted_intents=[m.intent for m in remaining_matches],
            match_results=remaining_matches,
            investigator_match=merged_investigator,
            removed_names=[],
        )
