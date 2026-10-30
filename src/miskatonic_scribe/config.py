"""Application and LLM fallback configuration data models."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, List

from dotenv import load_dotenv


def parse_allowed_user_ids(raw_value: str) -> FrozenSet[int]:
    """Parse a comma-separated string of Telegram numeric user IDs into a FrozenSet[int]."""
    ids: set[int] = set()
    for part in raw_value.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            ids.add(int(cleaned))
        except ValueError:
            continue
    return frozenset(ids)


@dataclass(frozen=True)
class GeminiModelConfig:
    """Data model for Google Gemini LLM configuration with fallback chain support."""

    api_key: str
    primary_model: str = "gemini-3.5-flash"
    fallback_models: List[str] = field(
        default_factory=lambda: [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
        ]
    )
    max_retries: int = 2
    initial_delay: float = 1.0

    @property
    def candidate_models(self) -> List[str]:
        """Return ordered unique list of models starting with primary then fallbacks."""
        seen: set[str] = set()
        ordered: List[str] = []
        for model in [self.primary_model, *self.fallback_models]:
            cleaned = model.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                ordered.append(cleaned)
        return ordered

    @classmethod
    def from_env(cls) -> GeminiModelConfig:
        """Build GeminiModelConfig from environment variables."""
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        primary_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
        raw_fallbacks = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-latest",
        )
        fallback_models = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]

        try:
            max_retries = max(0, int(os.getenv("GEMINI_MAX_RETRIES", "2")))
        except ValueError:
            max_retries = 2

        try:
            initial_delay = max(0.0, float(os.getenv("GEMINI_INITIAL_DELAY", "1.0")))
        except ValueError:
            initial_delay = 1.0

        return cls(
            api_key=api_key,
            primary_model=primary_model,
            fallback_models=fallback_models,
            max_retries=max_retries,
            initial_delay=initial_delay,
        )


@dataclass(frozen=True)
class AudioMaintenanceConfig:
    """Configuration for local audio file storage and retention policy."""

    storage_dir: Path = Path("temp_audio")
    max_files: int = 5
    max_age_days: int = 3

    @classmethod
    def from_env(cls) -> AudioMaintenanceConfig:
        """Build AudioMaintenanceConfig from environment variables."""
        storage_dir = Path(os.getenv("AUDIO_STORAGE_DIR", "temp_audio"))
        try:
            max_files = max(1, int(os.getenv("AUDIO_MAX_FILES", "5")))
        except ValueError:
            max_files = 5

        try:
            max_age_days = max(1, int(os.getenv("AUDIO_MAX_AGE_DAYS", "3")))
        except ValueError:
            max_age_days = 3

        return cls(
            storage_dir=storage_dir,
            max_files=max_files,
            max_age_days=max_age_days,
        )


@dataclass(frozen=True)
class AppConfig:
    """Root application configuration."""

    telegram_bot_token: str
    gemini: GeminiModelConfig
    audio: AudioMaintenanceConfig
    allowed_user_ids: FrozenSet[int] = field(default_factory=lambda: frozenset({1725371455}))
    telegram_connect_timeout: float = 30.0
    telegram_read_timeout: float = 60.0
    telegram_download_retries: int = 3
    transcription_mode: str = "whisper"
    whisper_model_name: str = "base"
    whisper_device: str = "cpu"
    arkhamdb_cards_path: Path = Path("cards.json")
    arkhamdb_api_url: str = "https://arkhamdb.com/api/public/cards/"
    arkhamdb_refresh_days: int = 30
    arkhamdb_username: str = ""
    arkhamdb_password: str = ""
    arkhamdb_access_token: str = ""
    fuzzy_match_threshold: int = 65
    user_collection_db_path: Path = Path("data/user_collections.json")
    log_level: str = "INFO"
    log_file_path: Path = Path("data/logs/miskatonic_scribe.log")

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> AppConfig:
        """Load environment variables and instantiate AppConfig."""
        if env_file:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        try:
            threshold = int(os.getenv("FUZZY_MATCH_THRESHOLD", "65"))
            threshold = min(100, max(0, threshold))
        except ValueError:
            threshold = 65

        try:
            refresh_days = max(1, int(os.getenv("ARKHAMDB_REFRESH_DAYS", "30")))
        except ValueError:
            refresh_days = 30

        try:
            connect_timeout = max(5.0, float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "30.0")))
        except ValueError:
            connect_timeout = 30.0

        try:
            read_timeout = max(10.0, float(os.getenv("TELEGRAM_READ_TIMEOUT", "60.0")))
        except ValueError:
            read_timeout = 60.0

        try:
            download_retries = max(1, int(os.getenv("TELEGRAM_DOWNLOAD_RETRIES", "3")))
        except ValueError:
            download_retries = 3

        raw_allowed_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "1725371455")
        allowed_user_ids = parse_allowed_user_ids(raw_allowed_ids)

        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            gemini=GeminiModelConfig.from_env(),
            audio=AudioMaintenanceConfig.from_env(),
            allowed_user_ids=allowed_user_ids,
            telegram_connect_timeout=connect_timeout,
            telegram_read_timeout=read_timeout,
            telegram_download_retries=download_retries,
            transcription_mode=os.getenv("TRANSCRIPTION_MODE", "whisper").strip().lower() or "whisper",
            whisper_model_name=os.getenv("WHISPER_MODEL_NAME", "base").strip() or "base",
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu",
            arkhamdb_cards_path=Path(os.getenv("ARKHAMDB_CARDS_PATH", "cards.json")),
            arkhamdb_api_url=os.getenv(
                "ARKHAMDB_API_URL", "https://arkhamdb.com/api/public/cards/"
            ).strip(),
            arkhamdb_refresh_days=refresh_days,
            arkhamdb_username=os.getenv("ARKHAMDB_USERNAME", "").strip(),
            arkhamdb_password=os.getenv("ARKHAMDB_PASSWORD", "").strip(),
            arkhamdb_access_token=os.getenv("ARKHAMDB_ACCESS_TOKEN", "").strip(),
            fuzzy_match_threshold=threshold,
            user_collection_db_path=Path(
                os.getenv("USER_COLLECTION_DB_PATH", "data/user_collections.json")
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip() or "INFO",
            log_file_path=Path(
                os.getenv("LOG_FILE_PATH", "data/logs/miskatonic_scribe.log")
            ),
        )
