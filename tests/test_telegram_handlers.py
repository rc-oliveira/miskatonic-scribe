"""End-to-end tests for IAM (UserManager/ConfigManager), Admin Panel, Onboarding, and MiskatonicTelegramBot handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Coroutine, List, Tuple
from unittest.mock import AsyncMock, MagicMock

from scripts.seed_admin import main as seed_admin_main
from miskatonic_scribe.bot.telegram_handlers import MiskatonicTelegramBot
from miskatonic_scribe.config import AudioMaintenanceConfig
from miskatonic_scribe.domain.exceptions import LLMExtractionError, TranscriptionError
from miskatonic_scribe.domain.interfaces import IAudioTranscriber, ICardIntentExtractor
from miskatonic_scribe.domain.models import ExtractedCardIntent, ExtractedDeckPayload
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.infrastructure.audio_storage import LocalAudioStorageManager
from miskatonic_scribe.infrastructure.iam_managers import ConfigManager, UserManager
from miskatonic_scribe.infrastructure.user_collection_repository import (
    JsonUserCollectionRepository,
)
from miskatonic_scribe.logging_config import configure_logging
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher
from miskatonic_scribe.services.registration_pipeline import CardRegistrationPipeline

ADMIN_USER_ID = 1725371455


def _drive_coroutine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive a coroutine to completion deterministically without requiring OS loopback sockets."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    else:
        raise RuntimeError("Coroutine yielded an unexpected unresolved future.")


def _create_approved_user_manager(tmp_path: Path, admin_id: int = ADMIN_USER_ID) -> UserManager:
    users_file = tmp_path / "users.json"
    manager = UserManager(file_path=users_file)
    _drive_coroutine(manager.seed_admin(telegram_id=admin_id, first_name="Rodrigo", username="rodrigo"))
    return manager


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


def test_seed_admin_cli_and_singleton_managers(tmp_path: Path) -> None:
    """Verify scripts/seed_admin.py generates users.json with status='approved' and role='admin',
    and UserManager / ConfigManager singletons manage JSON state with asyncio.Lock()."""
    UserManager.reset_instance()
    ConfigManager.reset_instance()

    users_file = tmp_path / "users.json"
    config_file = tmp_path / "config.json"

    exit_code = seed_admin_main(
        ["--id", "123456789", "--name", "Rodrigo", "--username", "rodrigo", "--file", str(users_file)],
        runner=_drive_coroutine,
    )
    assert exit_code == 0
    assert users_file.exists()

    u_mgr = UserManager.get_instance(file_path=users_file)
    assert UserManager.get_instance() is u_mgr
    user = u_mgr.get_user("123456789")
    assert user is not None
    assert user["first_name"] == "Rodrigo"
    assert user["username"] == "rodrigo"
    assert user["status"] == "approved"
    assert user["role"] == "admin"
    assert "created_at" in user
    assert u_mgr.is_approved(123456789) is True
    assert u_mgr.is_admin(123456789) is True

    c_mgr = ConfigManager.get_instance(file_path=config_file)
    assert ConfigManager.get_instance() is c_mgr
    _drive_coroutine(c_mgr.set("transcription_mode", "hybrid"))
    assert config_file.exists()
    assert c_mgr.get("transcription_mode") == "hybrid"


def test_onboarding_wizard_convite_and_admin_approval_flow(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify /start warns unknown users, /convite sets status='pending' and notifies admins with
    approve_<id> / reject_<id> buttons, and admin clicking approve_<id> approves and notifies user."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    pipeline = CardRegistrationPipeline(
        transcriber=StubTranscriber("Shrivelling"),
        extractor=StubExtractor([ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=1)]),
        matcher=matcher,
    )
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        user_manager=user_manager,
        card_repo=repo,
        use_thread_pool=False,
    )

    new_user_id = 999888777
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    # 1. Unknown user sends /start -> told system is closed and to use /convite
    start_upd = MagicMock()
    start_upd.effective_user.id = new_user_id
    start_upd.effective_user.first_name = "Daisy"
    start_upd.effective_user.username = "daisy_walker"
    start_upd.message.reply_text = AsyncMock()

    _drive_coroutine(bot.start_command(start_upd, context))
    start_reply = start_upd.message.reply_text.call_args.args[0]
    assert "/convite" in start_reply

    # 2. Unknown user sends /convite -> saved as 'pending', receives "Solicitação enviada.", admin notified
    invite_upd = MagicMock()
    invite_upd.effective_user.id = new_user_id
    invite_upd.effective_user.first_name = "Daisy"
    invite_upd.effective_user.username = "daisy_walker"
    invite_upd.message.reply_text = AsyncMock()

    _drive_coroutine(bot.invite_command(invite_upd, context))
    invite_upd.message.reply_text.assert_awaited_once_with("Solicitação enviada.")

    pending_record = user_manager.get_user(new_user_id)
    assert pending_record is not None
    assert pending_record["status"] == "pending"
    assert pending_record["role"] == "user"

    context.bot.send_message.assert_awaited_once()
    admin_notify_kwargs = context.bot.send_message.call_args.kwargs
    assert admin_notify_kwargs["chat_id"] == ADMIN_USER_ID
    assert "Daisy" in admin_notify_kwargs["text"]
    kb = admin_notify_kwargs["reply_markup"].inline_keyboard
    assert kb[0][0].callback_data == f"approve_{new_user_id}"
    assert kb[0][1].callback_data == f"reject_{new_user_id}"

    # 3. Admin clicks approve_<new_user_id>
    context.bot.send_message.reset_mock()
    approve_cb = MagicMock()
    approve_cb.effective_user.id = ADMIN_USER_ID
    approve_cb.effective_user.username = "rodrigo"
    approve_cb.callback_query.data = f"approve_{new_user_id}"
    approve_cb.callback_query.answer = AsyncMock()
    approve_cb.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_approval_callback(approve_cb, context))

    assert user_manager.is_approved(new_user_id) is True
    approve_cb.callback_query.edit_message_text.assert_awaited_once()
    assert "aprovado" in approve_cb.callback_query.edit_message_text.call_args.args[0]

    # Soliciting user was notified of approval
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.call_args.kwargs["chat_id"] == new_user_id
    assert "Acesso Aprovado" in context.bot.send_message.call_args.kwargs["text"]


def test_admin_panel_navigation_and_access_revocation(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify /admin is restricted to role=='admin', lists pending/approved users, and revokes access."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
    _drive_coroutine(user_manager.request_invite(telegram_id=555111, first_name="Roland", username="roland"))
    _drive_coroutine(user_manager.set_user_status(555111, "approved"))
    _drive_coroutine(user_manager.request_invite(telegram_id=666222, first_name="Wendy", username="wendy"))

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
        user_manager=user_manager,
        card_repo=repo,
        use_thread_pool=False,
    )

    context = MagicMock()

    # 1. Non-admin approved user (Roland, 555111) tries /admin -> blocked!
    non_admin_upd = MagicMock()
    non_admin_upd.effective_user.id = 555111
    non_admin_upd.callback_query = None
    non_admin_upd.message.reply_text = AsyncMock()
    _drive_coroutine(bot.admin_command(non_admin_upd, context))
    assert "exclusivo para administradores" in non_admin_upd.message.reply_text.call_args.args[0]

    # 2. Admin (1725371455) opens /admin
    admin_upd = MagicMock()
    admin_upd.effective_user.id = ADMIN_USER_ID
    admin_upd.callback_query = None
    admin_upd.message.reply_text = AsyncMock()
    _drive_coroutine(bot.admin_command(admin_upd, context))
    admin_reply = admin_upd.message.reply_text.call_args.args[0]
    assert "Painel de Administração" in admin_reply

    # 3. Admin lists pending users (admin:list_pending) -> sees Wendy (666222)
    cb_pending = MagicMock()
    cb_pending.effective_user.id = ADMIN_USER_ID
    cb_pending.callback_query.data = "admin:list_pending"
    cb_pending.callback_query.answer = AsyncMock()
    cb_pending.callback_query.edit_message_text = AsyncMock()
    _drive_coroutine(bot.handle_admin_callback(cb_pending, context))
    assert "Wendy" in cb_pending.callback_query.edit_message_text.call_args.args[0]

    # 4. Admin lists approved users (admin:list_approved) -> sees Roland (555111)
    cb_approved = MagicMock()
    cb_approved.effective_user.id = ADMIN_USER_ID
    cb_approved.callback_query.data = "admin:list_approved"
    cb_approved.callback_query.answer = AsyncMock()
    cb_approved.callback_query.edit_message_text = AsyncMock()
    _drive_coroutine(bot.handle_admin_callback(cb_approved, context))
    assert "Roland" in cb_approved.callback_query.edit_message_text.call_args.args[0]

    # 5. Admin revokes Roland's access (admin:revoke_555111) -> status becomes 'rejected'
    cb_revoke = MagicMock()
    cb_revoke.effective_user.id = ADMIN_USER_ID
    cb_revoke.callback_query.data = "admin:revoke_555111"
    cb_revoke.callback_query.answer = AsyncMock()
    cb_revoke.callback_query.edit_message_text = AsyncMock()
    _drive_coroutine(bot.handle_admin_callback(cb_revoke, context))

    assert user_manager.is_approved(555111) is False
    assert user_manager.get_user(555111)["status"] == "rejected"


def test_unauthorized_user_is_blocked_across_all_handlers(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify users without status == 'approved' in UserManager cannot trigger audio download, commands, or callbacks."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
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
        user_manager=user_manager,
        card_repo=repo,
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
    """Verify full flow for approved user: Voice -> Whisper -> Gemini -> Fuzzy Match -> Confirm -> /colecao."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
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
        user_manager=user_manager,
        card_repo=repo,
        use_thread_pool=False,
    )

    update = MagicMock()
    update.effective_user.id = ADMIN_USER_ID
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

    # Simulate approved user clicking "✅ Confirmar"
    cb_update = MagicMock()
    cb_update.effective_user.id = ADMIN_USER_ID
    cb_update.effective_user.username = "rodrigo"
    cb_update.callback_query.data = f"confirm:{batch_id}"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, context))

    cb_update.callback_query.edit_message_text.assert_awaited_once()
    confirm_text = cb_update.callback_query.edit_message_text.call_args.args[0]
    assert "Registro Confirmado!" in confirm_text
    assert "3 cópia(s) adicionada(s)" in confirm_text

    saved = collection_repo.get_user_collection(ADMIN_USER_ID)
    assert len(saved) == 2
    assert {c["code"]: c["quantity"] for c in saved} == {"01020": 1, "02066": 2}

    # Test /colecao command output
    col_update = MagicMock()
    col_update.effective_user.id = ADMIN_USER_ID
    col_update.callback_query = None
    col_update.message.reply_text = AsyncMock()
    _drive_coroutine(bot.collection_command(col_update, context))
    col_reply = col_update.message.reply_text.call_args.args[0]
    assert "02066" in col_reply
    assert "01020" in col_reply

    # Test /atualizar_cartas command output
    sync_status_msg = MagicMock()
    sync_status_msg.edit_text = AsyncMock()
    sync_update = MagicMock()
    sync_update.effective_user.id = ADMIN_USER_ID
    sync_update.callback_query = None
    sync_update.message.reply_text = AsyncMock(return_value=sync_status_msg)
    repo.refresh_cards = MagicMock(return_value=repo.load_cards())  # type: ignore[method-assign]
    _drive_coroutine(bot.sync_cards_command(sync_update, context))
    assert "atualizada com sucesso" in sync_status_msg.edit_text.call_args.args[0]


def test_telegram_cancel_callback_discards_batch(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify clicking '❌ Cancelar' discards the pending batch without saving."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
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
        user_manager=user_manager,
        card_repo=repo,
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
    cb_update.effective_user.id = ADMIN_USER_ID
    cb_update.callback_query.data = "cancel:abc12345"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, context))

    cb_update.callback_query.edit_message_text.assert_awaited_once_with(
        "🚫 Registro cancelado pelo usuário."
    )
    assert collection_repo.get_user_collection(ADMIN_USER_ID) == []


def test_telegram_handles_transcription_and_llm_errors_gracefully(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify user receives clear feedback when Whisper or Gemini fails."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
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
        user_manager=user_manager,
        use_thread_pool=False,
    )

    update = MagicMock()
    update.effective_user.id = ADMIN_USER_ID
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
        user_manager=user_manager,
        use_thread_pool=False,
    )
    status_msg.edit_text.reset_mock()
    _drive_coroutine(bot_llm_fail.handle_voice_or_audio(update, MagicMock()))
    assert "Falha ao processar cartas via Gemini" in status_msg.edit_text.call_args.args[0]


def test_telegram_audio_download_retries_on_timeout_and_logs_to_file(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify transient 'Timed out' on Telegram audio download is retried and logged to disk."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
    log_file = tmp_path / "logs" / "miskatonic_scribe.log"
    configure_logging(level="INFO", log_file_path=log_file)

    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo)
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    sleeps: list[float] = []
    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=CardRegistrationPipeline(
            transcriber=StubTranscriber("Duas Machetes"),
            extractor=StubExtractor([ExtractedCardIntent(nome="Machete", nivel=0, quantidade=2)]),
            matcher=matcher,
        ),
        collection_repo=collection_repo,
        user_manager=user_manager,
        connect_timeout=30.0,
        read_timeout=60.0,
        download_retries=3,
        log_file_path=log_file,
        use_thread_pool=False,
        sleep_fn=sleeps.append,
    )

    update = MagicMock()
    update.effective_user.id = ADMIN_USER_ID
    update.callback_query = None
    update.message.audio = None
    update.message.voice.file_unique_id = "timeoutRetry1"
    update.message.voice.file_id = "file_id_123"
    update.message.voice.file_size = 18432
    update.message.voice.duration = 4

    attempts = 0

    async def flaky_download_to_drive(custom_path: str, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("Timed out")
        Path(custom_path).write_bytes(b"OggS-recovered")

    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock(side_effect=flaky_download_to_drive)
    update.message.voice.get_file = AsyncMock(return_value=telegram_file)

    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)
    context = MagicMock()
    context.user_data = {}

    _drive_coroutine(bot.handle_voice_or_audio(update, context))

    assert attempts == 3
    assert sleeps == [1.5, 3.0]
    sent_text = status_msg.edit_text.call_args.args[0]
    assert "2x Machete (Nível 0) - ID: 01020" in sent_text

    # Verify persistent log file contains the retry warnings and can be queried via /logs
    assert log_file.exists()
    log_contents = log_file.read_text(encoding="utf-8")
    assert "Telegram audio download failed on attempt 1/3" in log_contents
    assert "Timed out" in log_contents

    logs_update = MagicMock()
    logs_update.effective_user.id = ADMIN_USER_ID
    logs_update.callback_query = None
    logs_update.message.reply_text = AsyncMock()
    _drive_coroutine(bot.logs_command(logs_update, context))
    logs_reply = logs_update.message.reply_text.call_args.args[0]
    assert "Últimos Logs:" in logs_reply
    assert "Timed out" in logs_reply


class StubDeckPayloadExtractor(ICardIntentExtractor):
    def __init__(self, payloads: List[ExtractedDeckPayload]) -> None:
        self._payloads = list(payloads)

    def extract_cards(self, transcribed_text: str) -> Tuple[List[ExtractedCardIntent], str]:
        payload = self._payloads.pop(0)
        return payload.cartas, "gemini-3.5-flash"

    def extract_deck_payload(
        self,
        transcribed_text: str | None = None,
        audio_path: Path | None = None,
    ) -> Tuple[ExtractedDeckPayload, str]:
        return self._payloads.pop(0), "gemini-3.5-flash"


def test_telegram_text_correction_merges_pending_batch_and_exports_dek(
    tmp_path: Path,
    cards_json_file: Path,
) -> None:
    """Verify user can send a voice note (which misheard 'Astral Travel'), then send a text message
    to remove 'Astral Travel' and add '2x I've had worse… (lvl 2)', and upon confirming receive the .o8d file."""
    user_manager = _create_approved_user_manager(tmp_path, admin_id=ADMIN_USER_ID)
    audio_storage = LocalAudioStorageManager(
        config=AudioMaintenanceConfig(storage_dir=tmp_path / "audio", max_files=5, max_age_days=3)
    )
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    collection_repo = JsonUserCollectionRepository(db_path=tmp_path / "collections.json")

    extractor = StubDeckPayloadExtractor(
        [
            ExtractedDeckPayload(
                transcricao="Deck da Ana Estandley, dois definhar e dois faixão arranhão",
                investigador="Diana Stanley",
                cartas=[
                    ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=2),
                    ExtractedCardIntent(nome="Astral Travel", nivel=0, quantidade=2),
                ],
                remover=[],
            ),
            ExtractedDeckPayload(
                transcricao="trocar Astral Travel por 2 Foi só um arranhão nível 2",
                investigador=None,
                cartas=[
                    ExtractedCardIntent(nome="I've had worse…", nivel=2, quantidade=2),
                ],
                remover=["Astral Travel"],
            ),
        ]
    )

    pipeline = CardRegistrationPipeline(
        transcriber=StubTranscriber("Deck da Ana Estandley, dois definhar e dois faixão arranhão"),
        extractor=extractor,
        matcher=matcher,
        transcription_mode="whisper",
    )
    bot = MiskatonicTelegramBot(
        audio_storage=audio_storage,
        pipeline=pipeline,
        collection_repo=collection_repo,
        user_manager=user_manager,
        card_repo=repo,
        use_thread_pool=False,
    )

    context = MagicMock()
    context.user_data = {}

    # Step 1: Send voice message
    voice_update = MagicMock()
    voice_update.effective_user.id = ADMIN_USER_ID
    voice_update.callback_query = None
    voice_update.message.audio = None
    voice_update.message.voice.file_unique_id = "voiceDiana1"
    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock(
        side_effect=lambda custom_path, **kw: Path(custom_path).write_bytes(b"OggS")
    )
    voice_update.message.voice.get_file = AsyncMock(return_value=telegram_file)
    voice_status = MagicMock()
    voice_status.edit_text = AsyncMock()
    voice_update.message.reply_text = AsyncMock(return_value=voice_status)

    _drive_coroutine(bot.handle_voice_or_audio(voice_update, context))
    first_msg = voice_status.edit_text.call_args.args[0]
    assert "Diana Stanley" in first_msg
    assert "05004" in first_msg
    assert "Astral Travel" in first_msg

    # Step 2: Send text message to replace Astral Travel with 2x I've had worse… (lvl 2)
    text_update = MagicMock()
    text_update.effective_user.id = ADMIN_USER_ID
    text_update.callback_query = None
    text_update.message.text = "trocar Astral Travel por 2 Foi só um arranhão nível 2"
    text_status = MagicMock()
    text_status.edit_text = AsyncMock()
    text_update.message.reply_text = AsyncMock(return_value=text_status)

    _drive_coroutine(bot.handle_text_message(text_update, context))
    second_msg = text_status.edit_text.call_args.args[0]
    assert "Diana Stanley" in second_msg
    assert "03034" not in second_msg
    assert "2x Astral Travel" not in second_msg
    assert "2x I've had worse… (Nível 2) - ID: 02261" in second_msg

    # Step 3: Confirm the updated batch and verify .o8d file is sent via reply_document
    active_batch_id = context.user_data["active_batch_id"]
    cb_update = MagicMock()
    cb_update.effective_user.id = ADMIN_USER_ID
    cb_update.effective_user.username = "rodrigo"
    cb_update.callback_query.data = f"confirm:{active_batch_id}"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()
    cb_update.callback_query.message.reply_document = AsyncMock()

    _drive_coroutine(bot.handle_confirmation_callback(cb_update, context))

    cb_update.callback_query.message.reply_document.assert_awaited_once()
    doc_kwargs = cb_update.callback_query.message.reply_document.call_args.kwargs
    assert doc_kwargs["filename"].startswith("deck_diana_stanley_")
    assert doc_kwargs["filename"].endswith(".o8d")
    dek_bytes = doc_kwargs["document"].getvalue().decode("utf-8")
    assert "Diana Stanley" in dek_bytes
    assert "I've had worse…" in dek_bytes
