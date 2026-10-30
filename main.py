"""Main entrypoint for Miskatonic Scribe Telegram Bot."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure 'src' is importable when running `python main.py` directly
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from miskatonic_scribe.bot.telegram_handlers import (
    MiskatonicTelegramBot,
    build_telegram_application,
)
from miskatonic_scribe.config import AppConfig
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.infrastructure.audio_storage import LocalAudioStorageManager
from miskatonic_scribe.infrastructure.gemini_extractor import GeminiCardIntentExtractor
from miskatonic_scribe.infrastructure.user_collection_repository import (
    JsonUserCollectionRepository,
)
from miskatonic_scribe.infrastructure.whisper_transcriber import WhisperAudioTranscriber
from miskatonic_scribe.logging_config import configure_logging
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher
from miskatonic_scribe.services.registration_pipeline import CardRegistrationPipeline

logger = logging.getLogger(__name__)


def bootstrap_application(config: AppConfig | None = None) -> MiskatonicTelegramBot:
    """Wire all dependencies following Dependency Inversion and warm up ArkhamDB cache."""
    cfg = config or AppConfig.from_env()
    configure_logging(cfg.log_level)

    logger.info(
        "Initializing Miskatonic Scribe | Primary LLM: %s | Fallbacks: %s | Allowed User IDs: %s",
        cfg.gemini.primary_model,
        cfg.gemini.fallback_models,
        sorted(cfg.allowed_user_ids),
    )

    audio_storage = LocalAudioStorageManager(config=cfg.audio)
    card_repository = ArkhamDBCardRepository(
        cards_path=cfg.arkhamdb_cards_path,
        api_url=cfg.arkhamdb_api_url,
    )
    # Warm up local cards.json on startup (downloads from ArkhamDB if missing)
    card_repository.load_cards()

    transcriber = WhisperAudioTranscriber(
        model_name=cfg.whisper_model_name,
        device=cfg.whisper_device,
    )
    extractor = GeminiCardIntentExtractor(config=cfg.gemini)
    matcher = ArkhamFuzzyCardMatcher(
        repository=card_repository,
        similarity_threshold=cfg.fuzzy_match_threshold,
    )
    pipeline = CardRegistrationPipeline(
        transcriber=transcriber,
        extractor=extractor,
        matcher=matcher,
    )
    collection_repo = JsonUserCollectionRepository(db_path=cfg.user_collection_db_path)

    return MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        allowed_user_ids=cfg.allowed_user_ids,
    )


def main() -> None:
    """Run the Telegram bot polling loop."""
    cfg = AppConfig.from_env()
    controller = bootstrap_application(cfg)
    app = build_telegram_application(
        bot_token=cfg.telegram_bot_token,
        controller=controller,
    )
    logger.info("Miskatonic Scribe Bot is now listening for voice/audio messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
