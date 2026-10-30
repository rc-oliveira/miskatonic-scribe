"""LLM card extraction using Google Gemini with automatic retry and fallback model chain."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, List, Optional, Tuple

from pydantic import ValidationError

from miskatonic_scribe.config import GeminiModelConfig
from miskatonic_scribe.domain.exceptions import LLMExtractionError
from miskatonic_scribe.domain.interfaces import ICardIntentExtractor
from miskatonic_scribe.domain.models import ExtractedCardIntent

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Você é um assistente de Arkham Horror LCG. Receba este texto falho, "
    "extraia os nomes das cartas faladas, traduza obrigatoriamente para o nome "
    "oficial em Inglês e identifique o nível (XP). Retorne APENAS um JSON "
    'válido no formato: [{"nome": "Card Name", "nivel": 0, "quantidade": 1}]'
)

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class GeminiCardIntentExtractor(ICardIntentExtractor):
    """Extracts structured Arkham Horror LCG card intents using Gemini with model fallback."""

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
        """Strip markdown code blocks or surrounding prose to isolate the JSON array."""
        cleaned = raw_text.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", cleaned, flags=re.DOTALL)
        if fenced_match:
            return fenced_match.group(1).strip()

        bracket_start = cleaned.find("[")
        bracket_end = cleaned.rfind("]")
        if bracket_start != -1 and bracket_end != -1 and bracket_end >= bracket_start:
            return cleaned[bracket_start : bracket_end + 1]

        return cleaned

    @classmethod
    def _parse_and_validate(cls, raw_text: str) -> List[ExtractedCardIntent]:
        """Parse JSON string into validated ExtractedCardIntent models."""
        if not raw_text or not raw_text.strip():
            raise ValueError("Empty response received from Gemini model.")

        cleaned = cls._clean_json_payload(raw_text)
        parsed = json.loads(cleaned)

        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array of cards, got {type(parsed).__name__}.")

        intents: List[ExtractedCardIntent] = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError(f"Each element in JSON array must be an object, got {item!r}.")
            intents.append(ExtractedCardIntent.model_validate(item))

        return intents

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

    def _invoke_model(self, client: Any, model_name: str, transcribed_text: str) -> str:
        """Call Gemini model with the required system instruction."""
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            )
            response = client.models.generate_content(
                model=model_name,
                contents=transcribed_text,
                config=config,
            )
        except ImportError:
            # Fallback if a mock client is injected without google.genai installed
            response = client.models.generate_content(
                model=model_name,
                contents=transcribed_text,
                config={"system_instruction": SYSTEM_PROMPT, "temperature": 0.1},
            )

        text = getattr(response, "text", None)
        if text is None:
            raise ValueError(f"Model '{model_name}' returned response without text payload.")
        return str(text)

    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        """Extract card intents using primary model with retries and fallback models."""
        if not transcribed_text or not transcribed_text.strip():
            raise LLMExtractionError("Cannot extract cards from empty transcription text.")

        client = self._get_client()
        candidate_models = self._config.candidate_models
        errors_encountered: List[str] = []

        for model_name in candidate_models:
            total_attempts = self._config.max_retries + 1
            for attempt in range(1, total_attempts + 1):
                try:
                    logger.info(
                        "Requesting card extraction from Gemini model '%s' (attempt %d/%d)...",
                        model_name,
                        attempt,
                        total_attempts,
                    )
                    raw_output = self._invoke_model(client, model_name, transcribed_text)
                    intents = self._parse_and_validate(raw_output)
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
                        logger.info(
                            "Retrying model '%s' in %.2fs...",
                            model_name,
                            delay,
                        )
                        self._sleep_fn(delay)
                    else:
                        logger.warning(
                            "Exhausted retries for model '%s'. Switching to next fallback model if available.",
                            model_name,
                        )
                else:
                    logger.info(
                        "Successfully extracted %d card intent(s) using model '%s'.",
                        len(intents),
                        model_name,
                    )
                    return intents, model_name

        summary = "; ".join(errors_encountered)
        logger.error("All Gemini models failed to extract cards. Details: %s", summary)
        raise LLMExtractionError(
            f"Falha ao estruturar cartas via Gemini após tentar {candidate_models}. Erros: {summary}"
        )
