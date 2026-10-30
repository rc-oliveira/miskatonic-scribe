"""Tests for GeminiCardIntentExtractor including retries and fallback model chain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

import pytest

from miskatonic_scribe.config import GeminiModelConfig
from miskatonic_scribe.domain.exceptions import LLMExtractionError
from miskatonic_scribe.infrastructure.gemini_extractor import (
    SYSTEM_PROMPT,
    GeminiCardIntentExtractor,
)


@dataclass
class FakeGenAIResponse:
    text: str


class FakeModelsEndpoint:
    def __init__(self, behaviors: List[Any]) -> None:
        self.behaviors = list(behaviors)
        self.calls: List[dict[str, Any]] = []

    def generate_content(self, model: str, contents: str, config: Any) -> FakeGenAIResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        action = self.behaviors.pop(0)
        if isinstance(action, Exception):
            raise action
        return FakeGenAIResponse(text=str(action))


class FakeGenAIClient:
    def __init__(self, behaviors: List[Any]) -> None:
        self.models = FakeModelsEndpoint(behaviors)


def test_gemini_config_candidate_models_order() -> None:
    cfg = GeminiModelConfig(
        api_key="test-key",
        primary_model="gemini-3.5-flash",
        fallback_models=[
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
        ],
        max_retries=2,
        initial_delay=1.0,
    )
    assert cfg.candidate_models == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
    ]


def test_gemini_extracts_cards_with_primary_model() -> None:
    cfg = GeminiModelConfig(api_key="key", primary_model="gemini-3.5-flash", max_retries=1)
    client = FakeGenAIClient(
        [
            '[{"nome": "Shrivelling", "nivel": 3, "quantidade": 2}, '
            '{"nome": "Machete", "nivel": 0, "quantidade": 1}]'
        ]
    )
    extractor = GeminiCardIntentExtractor(config=cfg, client=client, sleep_fn=lambda _: None)

    intents, model_used = extractor.extract_cards("duas definhar nivel 3 e um facao")

    assert model_used == "gemini-3.5-flash"
    assert len(intents) == 2
    assert intents[0].nome == "Shrivelling"
    assert intents[0].nivel == 3
    assert intents[0].quantidade == 2
    assert intents[1].nome == "Machete"
    assert intents[1].nivel == 0
    assert intents[1].quantidade == 1
    assert len(client.models.calls) == 1
    # Verify system prompt was injected
    call_cfg = client.models.calls[0]["config"]
    sys_inst = (
        call_cfg.get("system_instruction")
        if isinstance(call_cfg, dict)
        else getattr(call_cfg, "system_instruction", None)
    )
    assert sys_inst == SYSTEM_PROMPT


def test_gemini_fallback_chain_on_429_and_503_errors() -> None:
    """When primary model hits 429/503 across all retries, extractor falls back to fallback models."""
    cfg = GeminiModelConfig(
        api_key="key",
        primary_model="gemini-3.5-flash",
        fallback_models=[
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
        ],
        max_retries=2,  # 3 attempts per model
        initial_delay=0.5,
    )
    sleeps: List[float] = []

    # 3 failures on gemini-3.5-flash (503 high demand),
    # 1 failure on gemini-3.5-flash-lite (429 rate limit),
    # then success on attempt 2 of gemini-3.5-flash-lite!
    client = FakeGenAIClient(
        [
            RuntimeError("503 Service Unavailable: high demand"),
            RuntimeError("503 Service Unavailable: high demand"),
            RuntimeError("503 Service Unavailable: high demand"),
            RuntimeError("429 Resource Exhausted: rate limit"),
            '```json\n[{"nome": "Dr. Milan Christopher", "nivel": 0, "quantidade": 1}]\n```',
        ]
    )

    extractor = GeminiCardIntentExtractor(config=cfg, client=client, sleep_fn=sleeps.append)
    intents, model_used = extractor.extract_cards("um doutor milan")

    assert model_used == "gemini-3.5-flash-lite"
    assert len(intents) == 1
    assert intents[0].nome == "Dr. Milan Christopher"
    assert [c["model"] for c in client.models.calls] == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    # Exponential backoff delays: 0.5, 1.0 on primary, then 0.5 on first fallback
    assert sleeps == [0.5, 1.0, 0.5]


def test_gemini_recovers_from_malformed_json_using_fallback() -> None:
    cfg = GeminiModelConfig(
        api_key="key",
        primary_model="gemini-3.5-flash",
        fallback_models=["gemini-3.5-flash-lite"],
        max_retries=0,
    )
    client = FakeGenAIClient(
        [
            "Desculpe, não entendi.",  # Invalid JSON on primary
            '[{"nome": "Shrivelling", "nivel": 5, "quantidade": 2}]',  # Valid on fallback
        ]
    )
    extractor = GeminiCardIntentExtractor(config=cfg, client=client, sleep_fn=lambda _: None)
    intents, model_used = extractor.extract_cards("duas shrivelling 5 de xp")

    assert model_used == "gemini-3.5-flash-lite"
    assert intents[0].nivel == 5


def test_gemini_raises_when_all_models_exhausted() -> None:
    cfg = GeminiModelConfig(
        api_key="key",
        primary_model="gemini-3.5-flash",
        fallback_models=["gemini-3.5-flash-lite"],
        max_retries=1,
    )
    client = FakeGenAIClient(
        [
            RuntimeError("503 overloaded"),
            RuntimeError("503 overloaded"),
            RuntimeError("429 quota exceeded"),
            RuntimeError("429 quota exceeded"),
        ]
    )
    extractor = GeminiCardIntentExtractor(config=cfg, client=client, sleep_fn=lambda _: None)

    with pytest.raises(LLMExtractionError, match="Falha ao estruturar cartas via Gemini"):
        extractor.extract_cards("duas machetes")


def test_gemini_extracts_full_deck_payload_with_investigator_and_audio(tmp_path: Path) -> None:
    """Verify extract_deck_payload parses investigator, cards, and removals from multimodal audio/text."""
    audio_file = tmp_path / "voice.ogg"
    audio_file.write_bytes(b"OggS-audio-data")

    cfg = GeminiModelConfig(api_key="key", primary_model="gemini-3.5-flash", max_retries=0)
    client = FakeGenAIClient(
        [
            '{"transcricao": "Deck da Diana Stanley, trocar Astral Travel por 2 Foi só um arranhão nivel 2", '
            '"investigador": "Diana Stanley", '
            '"cartas": [{"nome": "I\'ve had worse…", "nivel": 2, "quantidade": 2}], '
            '"remover": ["Astral Travel"]}'
        ]
    )
    extractor = GeminiCardIntentExtractor(config=cfg, client=client, sleep_fn=lambda _: None)

    payload, model_used = extractor.extract_deck_payload(audio_path=audio_file)
    assert model_used == "gemini-3.5-flash"
    assert payload.investigador == "Diana Stanley"
    assert len(payload.cartas) == 1
    assert payload.cartas[0].nome == "I've had worse…"
    assert payload.cartas[0].nivel == 2
    assert payload.remover == ["Astral Travel"]

