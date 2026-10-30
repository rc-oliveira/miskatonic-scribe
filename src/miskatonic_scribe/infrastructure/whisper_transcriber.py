"""Local audio transcription service using OpenAI Whisper."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from miskatonic_scribe.domain.exceptions import TranscriptionError
from miskatonic_scribe.domain.interfaces import IAudioTranscriber

logger = logging.getLogger(__name__)

ARKHAM_WHISPER_PROMPT = (
    "Arkham Horror LCG cartas em português e inglês: "
    "Shrivelling, Machete, Dr. Milan Christopher, Ward of Protection, "
    "Deduction, Leo De Luca, Charisma, Beat Cop, Rite of Seeking, "
    "nível 0, nível 1, nível 2, nível 3, nível 4, nível 5, XP, duas cópias, uma cópia."
)


class WhisperAudioTranscriber(IAudioTranscriber):
    """Transcribes .ogg / audio files locally using openai-whisper ('base' model by default)."""

    def __init__(
        self,
        model_name: str = "base",
        device: str = "cpu",
        model_loader: Optional[Callable[[str, str], Any]] = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._model_loader = model_loader
        self._model: Any = None

    def _get_model(self) -> Any:
        """Lazily load and cache the Whisper model."""
        if self._model is None:
            logger.info(
                "Loading local Whisper model '%s' on device '%s'...",
                self._model_name,
                self._device,
            )
            try:
                if self._model_loader is not None:
                    self._model = self._model_loader(self._model_name, self._device)
                else:
                    import whisper  # type: ignore[import-untyped]

                    self._model = whisper.load_model(self._model_name, device=self._device)
            except Exception as exc:
                logger.error("Failed to load Whisper model '%s': %s", self._model_name, exc)
                raise TranscriptionError(
                    f"Unable to load Whisper model '{self._model_name}': {exc}"
                ) from exc
            else:
                logger.info("Whisper model '%s' loaded successfully.", self._model_name)
        return self._model

    def transcribe(self, audio_path: Path) -> str:
        """Convert a local audio file into text using Whisper."""
        if not audio_path.exists():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        model = self._get_model()
        logger.info("Transcribing audio file '%s' with Whisper...", audio_path.name)

        try:
            result = model.transcribe(
                str(audio_path),
                fp16=False,
                initial_prompt=ARKHAM_WHISPER_PROMPT,
            )
        except Exception as exc:
            logger.error("Whisper transcription failed for '%s': %s", audio_path.name, exc)
            raise TranscriptionError(f"Whisper failed to transcribe audio: {exc}") from exc
        else:
            raw_text = str(result.get("text", "") if isinstance(result, dict) else "").strip()
            if not raw_text:
                logger.warning("Whisper returned empty transcription for '%s'.", audio_path.name)
                raise TranscriptionError(
                    "Não foi possível detectar fala no áudio enviado (transcrição vazia)."
                )
            logger.info("Transcription complete (%d chars): %s", len(raw_text), raw_text)
            return raw_text
