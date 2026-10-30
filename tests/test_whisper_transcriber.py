"""Tests for WhisperAudioTranscriber."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from miskatonic_scribe.domain.exceptions import TranscriptionError
from miskatonic_scribe.infrastructure.whisper_transcriber import WhisperAudioTranscriber


class DummyWhisperModel:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[str] = []

    def transcribe(self, audio_path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(audio_path)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_whisper_transcribes_mixed_pt_en_jargon(tmp_path: Path) -> None:
    audio_file = tmp_path / "sample.ogg"
    audio_file.write_bytes(b"fake-ogg")

    dummy_model = DummyWhisperModel(
        {"text": "  Coloca dois Shrivelling nível 3 e um Facão nível zero  "}
    )
    load_count = 0

    def loader(name: str, device: str) -> DummyWhisperModel:
        nonlocal load_count
        load_count += 1
        assert name == "base"
        return dummy_model

    transcriber = WhisperAudioTranscriber(model_name="base", device="cpu", model_loader=loader)
    text1 = transcriber.transcribe(audio_file)
    text2 = transcriber.transcribe(audio_file)

    assert text1 == "Coloca dois Shrivelling nível 3 e um Facão nível zero"
    assert text2 == text1
    assert load_count == 1  # Model cached after first load


def test_whisper_raises_when_file_missing(tmp_path: Path) -> None:
    transcriber = WhisperAudioTranscriber(
        model_loader=lambda n, d: DummyWhisperModel({"text": "ok"})
    )
    with pytest.raises(TranscriptionError, match="not found"):
        transcriber.transcribe(tmp_path / "nonexistent.ogg")


def test_whisper_raises_on_empty_speech(tmp_path: Path) -> None:
    audio_file = tmp_path / "silent.ogg"
    audio_file.write_bytes(b"silent")

    transcriber = WhisperAudioTranscriber(
        model_loader=lambda n, d: DummyWhisperModel({"text": "   "})
    )
    with pytest.raises(TranscriptionError, match="transcrição vazia"):
        transcriber.transcribe(audio_file)


def test_whisper_raises_on_runtime_exception(tmp_path: Path) -> None:
    audio_file = tmp_path / "corrupt.ogg"
    audio_file.write_bytes(b"corrupt")

    transcriber = WhisperAudioTranscriber(
        model_loader=lambda n, d: DummyWhisperModel(RuntimeError("ffmpeg decode error"))
    )
    with pytest.raises(TranscriptionError, match="ffmpeg decode error"):
        transcriber.transcribe(audio_file)
