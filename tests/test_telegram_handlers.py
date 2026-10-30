"""End-to-end tests for CardRegistrationPipeline and MiskatonicTelegramBot handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Coroutine, List, Tuple
from unittest.mock import AsyncMock, MagicMock

from miskatonic_scribe.bot.telegram_handlers import MiskatonicTelegramBot
from miskatonic_scribe.config import AudioMaintenanceConfig, parse_allowed_user_ids
from miskatonic_scribe.domain.exceptions import LLMExtractionError, TranscriptionError
from miskatonic_scribe.domain.interfaces import IAudioTranscriber, ICardIntentExtractor
from miskatonic_scribe.domain.models import ExtractedCardIntent
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.infrastructure.audio_storage import LocalAudioStorageManager
from miskatonic_scribe.infrastructure.user_collection_repository import (
    JsonUserCollectionRepository,
)
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher
from miskatonic_scribe.services.registration_pipeline import CardRegistrationPipeline

AUTHORIZED_USER_ID = 1725371455


def _drive_coroutine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive a coroutine to completion deterministically without requiring OS loopback sockets."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    else:
        raise RuntimeError("Coroutine yielded an unexpected unresolved future.")


class StubTranscriber(IAudioTranscriber):
    def __init__(self, text_or_exc: str | Exception) -> None:
        self._text_or_exc = text_or_exc
        self.called = False

    def transcribe(self, audio_path: Path) -> str:
        self.called = True
        assert audio_path.exists()
        if isinstance(self._text_or_exc, Exception):
            raise self._text_or_exc
        return self._text_or_exc


class StubExtractor(ICardIntentExtractor):
    def __init__(
        self,
        intents_or_exc: List[ExtractedCardIntent] | Exception,
        model: str = "gemini-3.5-flash",
    ) -> None:
        self._intents_or_exc = intents_or_exc
        self._model = model
        self.called = False

    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        self.called = True
        if isinstance(self._intents_or_exc, Exception):
            raise self._intents_or_exc
        return self._intents_or_exc, self._model


def test_parse_allowed_user_ids_from_csv() -> None:
    """Verify comma-separated Telegram user IDs are parsed properly."""
    parsed = parse_allowed_user_ids("1725371455, 12345678, invalid, ")
    assert parsed == frozenset({1725371455, 12345678})


def test_unauthorized_user_is_blocked_across_all_handlers(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify users not in TELEGRAM_ALLOWED_USER_IDS cannot trigger audio download, commands, or callbacks."""
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    transcriber = StubTranscriber("Coloca duas Shrivelling")
    extractor = StubExtractor([ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=2)])
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    pipeline = CardRegistrationPipeline(
        transcriber=transcriber,
        extractor=extractor,
        matcher=matcher,
    )
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        allowed_user_ids={AUTHORIZED_USER_ID},
        use_thread_pool=False,
    )

    # 1. Unauthorized voice message attempt
    unauth_update = MagicMock()
    unauth_update.effective_user.id = 888888888
    unauth_update.effective_user.username = "cultist_intruder"
    unauth_update.callback_query = None
    unauth_update.message.reply_text = AsyncMock()
    unauth_update.message.voice.get_file = AsyncMock()

    _drive_coroutine(bot.handle_voice_or_audio(unauth_update, MagicMock()))

    unauth_update.message.reply_text.assert_awaited_once()
    assert "Acesso negado" in unauth_update.message.reply_text.call_args.args[0]
    unauth_update.message.voice.get_file.assert_not_called()
    assert transcriber.called is False
    assert extractor.called is False

    # 2. Unauthorized callback query attempt
    cb_update = MagicMock()
    cb_update.effective_user.id = 888888888
    cb_update.effective_user.username = "cultist_intruder"
    cb_update.callback_query.data = "confirm:abc12345"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, MagicMock()))
    cb_update.callback_query.answer.assert_awaited_once_with(
        "⛔ Acesso negado. Usuário não autorizado.",
        show_alert=True,
    )
    cb_update.callback_query.edit_message_text.assert_not_called()


def test_telegram_voice_to_confirmation_and_collection_flow(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify full flow for authorized user (1725371455): Voice -> Whisper -> Gemini -> Fuzzy Match -> Confirm -> /colecao."""
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    transcriber = StubTranscriber("Coloca duas Shrivelling nível 3 e um Facão nível 0")
    extractor = StubExtractor(
        [
            ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=2),
            ExtractedCardIntent(nome="Machete", nivel=0, quantidade=1),
        ]
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    pipeline = CardRegistrationPipeline(
        transcriber=transcriber,
        extractor=extractor,
        matcher=matcher,
    )
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        allowed_user_ids={AUTHORIZED_USER_ID},
        use_thread_pool=False,
    )

    update = MagicMock()
    update.effective_user.id = AUTHORIZED_USER_ID
    update.effective_user.username = "rodrigo"
    update.callback_query = None
    update.message.audio = None
    update.message.voice.file_unique_id = "voiceABC123"

    async def fake_download_to_drive(custom_path: str) -> None:
        Path(custom_path).write_bytes(b"OggS-test-voice")

    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock(side_effect=fake_download_to_drive)
    update.message.voice.get_file = AsyncMock(return_value=telegram_file)

    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)

    context = MagicMock()
    context.user_data = {}

    _drive_coroutine(bot.handle_voice_or_audio(update, context))

    status_msg.edit_text.assert_awaited_once()
    call_args = status_msg.edit_text.call_args
    sent_text = call_args.args[0]
    assert "2x Shrivelling (Nível 3) - ID: 02066" in sent_text
    assert "1x Machete (Nível 0) - ID: 01020" in sent_text
    assert "reply_markup" in call_args.kwargs

    pending = context.user_data.get("pending_batches", {})
    assert len(pending) == 1
    batch_id = next(iter(pending.keys()))

    # Simulate authorized user clicking "✅ Confirmar"
    cb_update = MagicMock()
    cb_update.effective_user.id = AUTHORIZED_USER_ID
    cb_update.effective_user.username = "rodrigo"
    cb_update.callback_query.data = f"confirm:{batch_id}"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, context))

    cb_update.callback_query.edit_message_text.assert_awaited_once()
    confirm_text = cb_update.callback_query.edit_message_text.call_args.args[0]
    assert "Registro Confirmado!" in confirm_text
    assert "3 cópia(s) adicionada(s)" in confirm_text

    saved = collection_repo.get_user_collection(AUTHORIZED_USER_ID)
    assert len(saved) == 2
    assert {c["code"]: c["quantity"] for c in saved} == {"01020": 1, "02066": 2}

    # Test /colecao command output
    col_update = MagicMock()
    col_update.effective_user.id = AUTHORIZED_USER_ID
    col_update.callback_query = None
    col_update.message.reply_text = AsyncMock()
    _drive_coroutine(bot.collection_command(col_update, context))
    col_reply = col_update.message.reply_text.call_args.args[0]
    assert "02066" in col_reply
    assert "01020" in col_reply


def test_telegram_cancel_callback_discards_batch(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify clicking '❌ Cancelar' discards the pending batch without saving."""
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    pipeline = CardRegistrationPipeline(
        transcriber=StubTranscriber("Machete"),
        extractor=StubExtractor([ExtractedCardIntent(nome="Machete", nivel=0, quantidade=1)]),
        matcher=matcher,
    )
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")
    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        allowed_user_ids={AUTHORIZED_USER_ID},
        use_thread_pool=False,
    )

    context = MagicMock()
    context.user_data = {
        "pending_batches": {
            "abc12345": matcher.match_all(
                [ExtractedCardIntent(nome="Machete", nivel=0, quantidade=1)]
            )
        }
    }

    cb_update = MagicMock()
    cb_update.effective_user.id = AUTHORIZED_USER_ID
    cb_update.callback_query.data = "cancel:abc12345"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, context))

    cb_update.callback_query.edit_message_text.assert_awaited_once_with(
        "🚫 Registro cancelado pelo usuário."
    )
    assert collection_repo.get_user_collection(AUTHORIZED_USER_ID) == []


def test_telegram_handles_transcription_and_llm_errors_gracefully(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify user receives clear feedback when Whisper or Gemini fails."""
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo)
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    # 1. Whisper error
    bot_whisper_fail = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=CardRegistrationPipeline(
            transcriber=StubTranscriber(TranscriptionError("Áudio inaudível")),
            extractor=StubExtractor([]),
            matcher=matcher,
        ),
        collection_repo=collection_repo,
        allowed_user_ids={AUTHORIZED_USER_ID},
        use_thread_pool=False,
    )

    update = MagicMock()
    update.effective_user.id = AUTHORIZED_USER_ID
    update.callback_query = None
    update.message.audio = None
    update.message.voice.file_unique_id = "err1"
    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock(
        side_effect=lambda custom_path: Path(custom_path).write_bytes(b"OggS")
    )
    update.message.voice.get_file = AsyncMock(return_value=telegram_file)
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)

    _drive_coroutine(bot_whisper_fail.handle_voice_or_audio(update, MagicMock()))
    assert "Falha na transcrição" in status_msg.edit_text.call_args.args[0]

    # 2. Gemini error
    bot_llm_fail = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=CardRegistrationPipeline(
            transcriber=StubTranscriber("Duas Machetes"),
            extractor=StubExtractor(LLMExtractionError("503 Service Unavailable")),
            matcher=matcher,
        ),
        collection_repo=collection_repo,
        allowed_user_ids={AUTHORIZED_USER_ID},
        use_thread_pool=False,
    )
    status_msg.edit_text.reset_mock()
    _drive_coroutine(bot_llm_fail.handle_voice_or_audio(update, MagicMock()))
    assert "Falha ao processar cartas via Gemini" in status_msg.edit_text.call_args.args[0]
