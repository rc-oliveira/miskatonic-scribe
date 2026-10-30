"""LLM card and investigator extraction using Google Gemini with multimodal audio and fallback chain."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from pydantic import ValidationError

from miskatonic_scribe.config import GeminiModelConfig
from miskatonic_scribe.domain.exceptions import LLMExtractionError
from miskatonic_scribe.domain.interfaces import ICardIntentExtractor
from miskatonic_scribe.domain.models import ExtractedCardIntent, ExtractedDeckPayload

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Você é um assistente de Arkham Horror LCG. Receba este texto falho, "
    "extraia os nomes das cartas faladas, traduza obrigatoriamente para o nome "
    "oficial em Inglês e identifique o nível (XP). Retorne APENAS um JSON "
    'válido no formato: [{"nome": "Card Name", "nivel": 0, "quantidade": 1}]'
)

DECK_EXTRACTION_PROMPT = (
    "Você é um assistente especialista em Arkham Horror: The Card Game (LCG). "
    "Você receberá uma transcrição ruidosa de áudio (ou o próprio áudio/mensagem de texto) "
    "misturando Português (PT-BR) e Inglês.\n"
    "Sua tarefa:\n"
    "1. Se um investigador for mencionado (ex: 'Ana Estandley' -> 'Diana Stanley', 'Rex', 'Daisy Walker'), "
    "identifique e traduza para o nome oficial em Inglês no campo 'investigador' (ou null se não mencionado).\n"
    "2. Extraia todas as cartas de jogador mencionadas, traduzindo OBRIGATORIAMENTE para o nome oficial em Inglês, "
    "com 'nivel' (XP, padrão 0 se não dito) e 'quantidade' (padrão 1 se não dito).\n"
    "   Atenção a traduções e aproximações fonéticas de PT-BR para EN:\n"
    "   - 'Foi só um arranhão' / 'faixão arranhão' / 'só um arranhão' -> \"I've had worse...\"\n"
    "   - 'Estoque de Emergência' / 'stock de emergência' -> 'Emergency Cache'\n"
    "   - 'Negar a Existência' -> 'Deny Existence'\n"
    "   - 'Olhar Hipnótico' -> 'Hypnotic Gaze'\n"
    "   - 'Símbolo de Proteção' -> 'Ward of Protection'\n"
    "   - 'Peitar' -> 'Guts' | 'Esquiva' -> 'Dodge' | 'Destemido' -> 'Fearless'\n"
    "   - 'Atraído pela Chama' -> 'Drawn to the Flame'\n"
    "   - 'Murchar' -> 'Wither' | 'Definhar' -> 'Shrivelling'\n"
    "   - 'Atrasar o Inevitável' -> 'Delay the Inevitable'\n"
    "   - 'Rosário Sagrado' -> 'Holy Rosary' | 'Fluxo Quântico' -> 'Quantum Flux'\n"
    "   - 'Sexto Sentido' -> 'Sixth Sense' | 'Quatro de Copas' -> 'Four of Cups'\n"
    "   - 'Lanterna' -> 'Flashlight' | 'Contramágica' -> 'Counterspell'\n"
    "   - 'Rito de Busca' -> 'Rite of Seeking' | 'Facão' -> 'Machete'\n"
    "3. Se o usuário pedir para remover ou substituir alguma carta (ex: 'remover Astral Travel' ou "
    "'trocar Astral Travel por 2 Foi só um arranhão'), liste os nomes em Inglês das cartas a remover em 'remover'.\n"
    "4. Retorne APENAS um JSON válido no formato:\n"
    '{"transcricao": "resumo do que foi ouvido/lido", "investigador": "Diana Stanley", '
    '"cartas": [{"nome": "Card Name", "nivel": 0, "quantidade": 1}], "remover": []}'
)

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class GeminiCardIntentExtractor(ICardIntentExtractor):
    """Extracts structured Arkham Horror LCG card and investigator intents using Gemini with model fallback."""

    def __init__(
        self,
        config: GeminiModelConfig,
        client: Optional[Any] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._client = client
        self._sleep_fn = sleep_fn

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._config.api_key:
                raise LLMExtractionError(
                    "GEMINI_API_KEY is not configured. Set it in the environment or .env file."
                )
            try:
                from google import genai

                self._client = genai.Client(api_key=self._config.api_key)
            except Exception as exc:
                raise LLMExtractionError(f"Failed to initialize Google GenAI client: {exc}") from exc
        return self._client

    @staticmethod
    def _clean_json_payload(raw_text: str) -> str:
        """Strip markdown code blocks or surrounding prose to isolate JSON object or array."""
        cleaned = raw_text.strip()
        fenced_match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", cleaned, flags=re.DOTALL)
        if fenced_match:
            return fenced_match.group(1).strip()

        # Find earliest '[' or '{' and matching closing bracket
        first_brace = cleaned.find("{")
        first_bracket = cleaned.find("[")

        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            end_brace = cleaned.rfind("}")
            if end_brace >= first_brace:
                return cleaned[first_brace : end_brace + 1]
        elif first_bracket != -1:
            end_bracket = cleaned.rfind("]")
            if end_bracket >= first_bracket:
                return cleaned[first_bracket : end_bracket + 1]

        return cleaned

    @classmethod
    def _parse_deck_payload(
        cls,
        raw_text: str,
        fallback_transcription: Optional[str] = None,
    ) -> ExtractedDeckPayload:
        """Parse either a JSON array `[...]` or a JSON object `{"investigador": ..., "cartas": [...]}`."""
        if not raw_text or not raw_text.strip():
            raise ValueError("Empty response received from Gemini model.")

        cleaned = cls._clean_json_payload(raw_text)
        parsed = json.loads(cleaned)

        if isinstance(parsed, list):
            intents: List[ExtractedCardIntent] = []
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError(f"Each element in JSON array must be an object, got {item!r}.")
                intents.append(ExtractedCardIntent.model_validate(item))
            return ExtractedDeckPayload(
                transcricao=fallback_transcription,
                investigador=None,
                cartas=intents,
                remover=[],
            )

        if isinstance(parsed, dict):
            payload = ExtractedDeckPayload.model_validate(parsed)
            if not payload.transcricao and fallback_transcription:
                payload = payload.model_copy(update={"transcricao": fallback_transcription})
            return payload

        raise ValueError(f"Expected JSON object or array of cards, got {type(parsed).__name__}.")

    @classmethod
    def _parse_and_validate(cls, raw_text: str) -> List[ExtractedCardIntent]:
        """Parse JSON string into validated ExtractedCardIntent models."""
        payload = cls._parse_deck_payload(raw_text)
        return payload.cartas

    @staticmethod
    def _is_rate_limit_or_overload(exc: Exception) -> bool:
        """Check whether an exception indicates HTTP 429/503 or transient server overload."""
        status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if isinstance(status_code, int) and status_code in RETRYABLE_STATUS_CODES:
            return True

        msg = str(exc).lower()
        retry_markers = (
            "429",
            "503",
            "rate limit",
            "resource_exhausted",
            "resourceexhausted",
            "unavailable",
            "overloaded",
            "high demand",
            "quota",
            "deadline",
            "timeout",
        )
        return any(marker in msg for marker in retry_markers)

    def _invoke_model(
        self,
        client: Any,
        model_name: str,
        contents: Any,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        """Call Gemini model with the given system instruction and contents."""
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                response_mime_type="application/json",
            )
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except ImportError:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config={"system_instruction": system_prompt, "temperature": 0.1},
            )

        text = getattr(response, "text", None)
        if text is None:
            raise ValueError(f"Model '{model_name}' returned response without text payload.")
        return str(text)

    def _build_multimodal_contents(
        self,
        transcribed_text: Optional[str],
        audio_path: Optional[Path],
    ) -> Any:
        """Construct contents payload for Gemini (text-only, audio-only, or hybrid audio+text)."""
        if audio_path is None:
            return transcribed_text or ""

        audio_bytes = audio_path.read_bytes()
        try:
            from google.genai import types

            audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
            if transcribed_text and transcribed_text.strip():
                prompt_text = (
                    f"Transcrição auxiliar do Whisper: '{transcribed_text}'. "
                    "Ouça também o áudio anexado para corrigir eventuais erros fonéticos de nomes "
                    "de cartas ou investigadores de Arkham Horror LCG."
                )
                return [audio_part, prompt_text]
            return [
                audio_part,
                "Transcreva o áudio e extraia o investigador e todas as cartas de Arkham Horror LCG mencionadas.",
            ]
        except ImportError:
            return transcribed_text or f"[audio:{audio_path.name}]"

    def _execute_with_fallback(
        self,
        contents: Any,
        system_prompt: str,
        fallback_transcription: Optional[str] = None,
    ) -> Tuple[ExtractedDeckPayload, str]:
        """Execute Gemini call across primary + fallback models with exponential backoff."""
        client = self._get_client()
        candidate_models = self._config.candidate_models
        errors_encountered: List[str] = []

        for model_name in candidate_models:
            total_attempts = self._config.max_retries + 1
            for attempt in range(1, total_attempts + 1):
                try:
                    logger.info(
                        "Requesting extraction from Gemini model '%s' (attempt %d/%d)...",
                        model_name,
                        attempt,
                        total_attempts,
                    )
                    raw_output = self._invoke_model(
                        client=client,
                        model_name=model_name,
                        contents=contents,
                        system_prompt=system_prompt,
                    )
                    payload = self._parse_deck_payload(
                        raw_output,
                        fallback_transcription=fallback_transcription,
                    )
                except (json.JSONDecodeError, ValidationError, ValueError) as parse_exc:
                    err_msg = f"{model_name} (attempt {attempt}) invalid JSON/schema: {parse_exc}"
                    logger.warning(err_msg)
                    errors_encountered.append(err_msg)
                    if attempt < total_attempts:
                        delay = self._config.initial_delay * (2 ** (attempt - 1))
                        self._sleep_fn(delay)
                except Exception as api_exc:
                    is_transient = self._is_rate_limit_or_overload(api_exc)
                    err_msg = (
                        f"{model_name} (attempt {attempt}) API error "
                        f"[transient={is_transient}]: {api_exc}"
                    )
                    logger.warning(err_msg)
                    errors_encountered.append(err_msg)

                    if attempt < total_attempts:
                        delay = self._config.initial_delay * (2 ** (attempt - 1))
                        logger.info("Retrying model '%s' in %.2fs...", model_name, delay)
                        self._sleep_fn(delay)
                    else:
                        logger.warning(
                            "Exhausted retries for model '%s'. Switching to next fallback model if available.",
                            model_name,
                        )
                else:
                    logger.info(
                        "Successfully extracted investigator=%r, %d card(s), %d removal(s) using model '%s'.",
                        payload.investigador,
                        len(payload.cartas),
                        len(payload.remover),
                        model_name,
                    )
                    return payload, model_name

        summary = "; ".join(errors_encountered)
        logger.error("All Gemini models failed to extract cards. Details: %s", summary)
        raise LLMExtractionError(
            f"Falha ao estruturar cartas via Gemini após tentar {candidate_models}. Erros: {summary}"
        )

    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        """Extract card intents using primary model with retries and fallback models."""
        if not transcribed_text or not transcribed_text.strip():
            raise LLMExtractionError("Cannot extract cards from empty transcription text.")

        payload, model_name = self._execute_with_fallback(
            contents=transcribed_text,
            system_prompt=SYSTEM_PROMPT,
            fallback_transcription=transcribed_text,
        )
        return payload.cartas, model_name

    def extract_deck_payload(
        self,
        transcribed_text: Optional[str] = None,
        audio_path: Optional[Path] = None,
    ) -> Tuple[ExtractedDeckPayload, str]:
        """Extract investigator, cards, and removals from text, audio, or both."""
        if (not transcribed_text or not transcribed_text.strip()) and audio_path is None:
            raise LLMExtractionError("Cannot extract deck payload without text or audio.")
        if audio_path is not None and not audio_path.exists():
            raise LLMExtractionError(f"Audio file not found for Gemini multimodal extraction: {audio_path}")

        contents = self._build_multimodal_contents(transcribed_text, audio_path)
        return self._execute_with_fallback(
            contents=contents,
            system_prompt=DECK_EXTRACTION_PROMPT,
            fallback_transcription=transcribed_text,
        )
