"""Telegram Bot v20+ handlers for voice/audio messages, retries, logging, and confirmation callbacks."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Collection, Dict, FrozenSet, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from miskatonic_scribe.domain.exceptions import (
    AudioStorageError,
    CardDatabaseError,
    LLMExtractionError,
    MiskatonicScribeError,
    PersistenceError,
    TranscriptionError,
)
from miskatonic_scribe.domain.interfaces import (
    IAudioStorageManager,
    ICardRepository,
    IUserCollectionRepository,
)
from miskatonic_scribe.domain.models import CardMatchResult, PipelineExecutionResult
from miskatonic_scribe.services.registration_pipeline import CardRegistrationPipeline

logger = logging.getLogger(__name__)


class MiskatonicTelegramBot:
    """Telegram Bot controller encapsulating message, audio download retry, and callback handlers."""

    def __init__(
        self,
        audio_storage: IAudioStorageManager,
        pipeline: CardRegistrationPipeline,
        collection_repo: IUserCollectionRepository,
        card_repo: Optional[ICardRepository] = None,
        allowed_user_ids: Optional[Collection[int]] = None,
        connect_timeout: float = 30.0,
        read_timeout: float = 60.0,
        download_retries: int = 3,
        log_file_path: Optional[Path] = Path("data/logs/miskatonic_scribe.log"),
        use_thread_pool: bool = True,
        sleep_fn: Optional[Callable[[float], Any]] = None,
    ) -> None:
        self._audio_storage = audio_storage
        self._pipeline = pipeline
        self._collection_repo = collection_repo
        self._card_repo = card_repo
        self._allowed_user_ids: FrozenSet[int] = (
            frozenset(allowed_user_ids)
            if allowed_user_ids is not None
            else frozenset({1725371455})
        )
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._download_retries = max(1, download_retries)
        self._log_file_path = log_file_path
        self._use_thread_pool = use_thread_pool
        self._sleep_fn = sleep_fn

    def is_user_authorized(self, user_id: Optional[int]) -> bool:
        """Return True only if user_id is present in the configured whitelist."""
        if user_id is None:
            return False
        return user_id in self._allowed_user_ids

    async def _ensure_authorized(self, update: Update) -> bool:
        """Validate effective_user against whitelist; log and reply if unauthorized."""
        user = update.effective_user
        user_id = user.id if user else None
        username = user.username if user else None

        if self.is_user_authorized(user_id):
            return True

        logger.warning(
            "Unauthorized access attempt blocked for user_id=%s (username=%s).",
            user_id,
            username,
        )
        if update.callback_query:
            await update.callback_query.answer(
                "⛔ Acesso negado. Usuário não autorizado.",
                show_alert=True,
            )
        elif update.message:
            await update.message.reply_text(
                "⛔ Acesso negado. Seu usuário não está autorizado a utilizar este bot."
            )
        return False

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help commands."""
        if not update.message:
            return
        if not await self._ensure_authorized(update):
            return
        welcome_text = (
            "🐙 *Miskatonic Scribe - Arkham Horror LCG*\n\n"
            "Envie uma *mensagem de voz* ou *arquivo de áudio* dizendo as cartas "
            "(em Português ou Inglês) e seus respectivos níveis (XP) e quantidades.\n\n"
            "Exemplo de áudio:\n"
            "_'Coloca duas cópias de Definhar nível 3 e dois Machetes nível 0'_\n\n"
            "Comandos disponíveis:\n"
            "• `/start` - Exibe esta mensagem de ajuda\n"
            "• `/colecao` - Lista as cartas já confirmadas e salvas na sua coleção\n"
            "• `/atualizar_cartas` - Força o download imediato da base do ArkhamDB\n"
            "• `/logs` - Exibe as últimas linhas do arquivo de log do servidor"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /logs command to return the tail of the persistent log file."""
        if not update.message:
            return
        if not await self._ensure_authorized(update):
            return

        if not self._log_file_path or not self._log_file_path.exists():
            await update.message.reply_text("📭 Arquivo de log ainda não encontrado no disco.")
            return

        try:
            lines = self._log_file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-25:]) if lines else "(vazio)"
            if len(tail) > 3800:
                tail = tail[-3800:]
            await update.message.reply_text(f"📋 *Últimos Logs:*\n```\n{tail}\n```", parse_mode="Markdown")
        except OSError as exc:
            logger.exception("Failed to read log file '%s': %s", self._log_file_path, exc)
            await update.message.reply_text(f"❌ Erro ao ler arquivo de log: {exc}")

    async def sync_cards_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /atualizar_cartas (/sync_cards) to force-refresh cards.json from ArkhamDB."""
        if not update.message:
            return
        if not await self._ensure_authorized(update):
            return
        if self._card_repo is None:
            await update.message.reply_text("⚠️ Repositório de cartas não configurado para atualização manual.")
            return

        status_msg = await update.message.reply_text(
            "🔄 Atualizando `cards.json` a partir do servidor do ArkhamDB...",
            parse_mode="Markdown",
        )
        try:
            if self._use_thread_pool:
                records = await asyncio.to_thread(self._card_repo.refresh_cards)
            else:
                records = self._card_repo.refresh_cards()
        except CardDatabaseError as exc:
            logger.exception("Manual ArkhamDB sync failed: %s", exc)
            await status_msg.edit_text(f"❌ Falha ao atualizar base do ArkhamDB: {exc}")
        else:
            await status_msg.edit_text(
                f"✅ Base do ArkhamDB (`cards.json`) atualizada com sucesso! Total de cartas carregadas: *{len(records)}*.",
                parse_mode="Markdown",
            )

    async def collection_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /colecao command to display the user's persisted cards."""
        if not update.message or not update.effective_user:
            return
        if not await self._ensure_authorized(update):
            return

        user_id = update.effective_user.id
        try:
            cards = self._collection_repo.get_user_collection(user_id)
        except PersistenceError as exc:
            logger.exception("Failed to load collection for user_id=%s: %s", user_id, exc)
            await update.message.reply_text(f"❌ Erro ao consultar sua coleção: {exc}")
            return

        if not cards:
            await update.message.reply_text(
                "📭 Sua coleção ainda está vazia. Envie um áudio para registrar cartas!"
            )
            return

        lines = ["📚 *Sua Coleção Registrada (Miskatonic Scribe):*\n"]
        for entry in cards:
            sub = f" [{entry['subname']}]" if entry.get("subname") else ""
            lines.append(
                f"• {entry['quantity']}x {entry['name']}{sub} (Nível {entry['xp']}) - ID: {entry['code']}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @staticmethod
    def format_confirmation_message(result: PipelineExecutionResult) -> str:
        """Build the Telegram response listing understood transcription and matched cards."""
        lines: List[str] = [
            f"🎙️ *Transcrição:* _{result.transcribed_text}_\n",
            "🃏 *Cartas Identificadas:*",
        ]
        for match in result.match_results:
            lines.append(f"• {match.format_telegram_line()}")

        lines.append("\nDeseja confirmar o registro destas cartas?")
        return "\n".join(lines)

    async def _download_telegram_audio(
        self,
        attachment: Any,
        local_audio_path: Path,
        user_id: Optional[int],
    ) -> None:
        """Download voice/audio from Telegram with generous timeouts, retry loop, and detailed logging."""
        file_id = getattr(attachment, "file_id", "unknown")
        file_unique_id = getattr(attachment, "file_unique_id", "unknown")
        file_size = getattr(attachment, "file_size", None)
        duration = getattr(attachment, "duration", None)
        mime_type = getattr(attachment, "mime_type", None)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._download_retries + 1):
            start_ts = time.monotonic()
            logger.info(
                "Starting Telegram audio download (attempt %d/%d) | user_id=%s | file_unique_id=%s | "
                "file_id=%s | size=%s bytes | duration=%ss | mime=%s | connect_timeout=%.1fs | read_timeout=%.1fs",
                attempt,
                self._download_retries,
                user_id,
                file_unique_id,
                file_id,
                file_size,
                duration,
                mime_type,
                self._connect_timeout,
                self._read_timeout,
            )
            try:
                try:
                    telegram_file = await attachment.get_file(
                        read_timeout=self._read_timeout,
                        connect_timeout=self._connect_timeout,
                    )
                except TypeError:
                    # Fallback for test mocks that do not accept timeout kwargs
                    telegram_file = await attachment.get_file()

                try:
                    await telegram_file.download_to_drive(
                        custom_path=str(local_audio_path),
                        read_timeout=self._read_timeout,
                        connect_timeout=self._connect_timeout,
                    )
                except TypeError:
                    await telegram_file.download_to_drive(custom_path=str(local_audio_path))

                elapsed = time.monotonic() - start_ts
                logger.info(
                    "Telegram audio downloaded successfully to '%s' in %.2fs (attempt %d/%d).",
                    local_audio_path,
                    elapsed,
                    attempt,
                    self._download_retries,
                )
                return
            except Exception as exc:
                elapsed = time.monotonic() - start_ts
                last_exc = exc
                if attempt < self._download_retries:
                    delay = 1.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "Telegram audio download failed on attempt %d/%d after %.2fs "
                        "(user_id=%s, file_unique_id=%s, error_type=%s: %s). Retrying in %.1fs...",
                        attempt,
                        self._download_retries,
                        elapsed,
                        user_id,
                        file_unique_id,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    if self._sleep_fn is not None:
                        res = self._sleep_fn(delay)
                        if inspect.isawaitable(res):
                            await res
                    else:
                        await asyncio.sleep(delay)
                else:
                    logger.exception(
                        "Exhausted all %d attempts downloading Telegram audio after %.2fs "
                        "(user_id=%s, file_unique_id=%s, file_id=%s, target='%s').",
                        self._download_retries,
                        elapsed,
                        user_id,
                        file_unique_id,
                        file_id,
                        local_audio_path,
                    )

        if last_exc is not None:
            raise last_exc

    async def handle_voice_or_audio(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Download voice/audio .ogg file, run pipeline, and ask user for confirmation."""
        if not update.message:
            return
        if not await self._ensure_authorized(update):
            return

        user_id = update.effective_user.id if update.effective_user else None
        attachment = update.message.voice or update.message.audio
        if attachment is None:
            await update.message.reply_text("⚠️ Nenhum arquivo de voz ou áudio encontrado na mensagem.")
            return

        status_msg = await update.message.reply_text(
            "⏳ Baixando áudio e consultando os arquivos da Universidade Miskatonic..."
        )

        # Step 1: Prepare local .ogg path (runs automatic 5-file / X-days cleanup routine)
        try:
            local_audio_path = self._audio_storage.prepare_audio_path(
                file_unique_id=attachment.file_unique_id,
                extension=".ogg",
            )
            await self._download_telegram_audio(
                attachment=attachment,
                local_audio_path=local_audio_path,
                user_id=user_id,
            )
        except AudioStorageError as exc:
            logger.exception("Audio storage error for user_id=%s: %s", user_id, exc)
            await status_msg.edit_text(f"❌ Erro ao gerenciar armazenamento local de áudio: {exc}")
            return
        except Exception as exc:
            await status_msg.edit_text(
                f"❌ Falha ao baixar o arquivo de áudio do Telegram após {self._download_retries} "
                f"tentativa(s) ({type(exc).__name__}: {exc}). Consulte `/logs` para mais detalhes."
            )
            return

        # Step 2: Run Transcription -> Gemini -> Fuzzy Matching
        try:
            if self._use_thread_pool:
                pipeline_result = await asyncio.to_thread(
                    self._pipeline.process_audio,
                    local_audio_path,
                )
            else:
                pipeline_result = self._pipeline.process_audio(local_audio_path)
        except TranscriptionError as exc:
            logger.error("TranscriptionError for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"🔇 Falha na transcrição do áudio: {exc}")
            return
        except LLMExtractionError as exc:
            logger.error("LLMExtractionError for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"🤖 Falha ao processar cartas via Gemini: {exc}")
            return
        except CardDatabaseError as exc:
            logger.error("CardDatabaseError for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"🗂️ Erro na base de dados do ArkhamDB: {exc}")
            return
        except MiskatonicScribeError as exc:
            logger.error("MiskatonicScribeError for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"❌ Erro no processamento: {exc}")
            return
        except Exception as exc:
            logger.exception("Unexpected error in voice pipeline for user_id=%s: %s", user_id, exc)
            await status_msg.edit_text(f"❌ Erro inesperado ao processar o áudio: {exc}")
            return

        if not pipeline_result.match_results:
            await status_msg.edit_text(
                f"🎙️ *Transcrição:* _{pipeline_result.transcribed_text}_\n\n"
                "⚠️ Nenhuma carta de Arkham Horror LCG foi identificada na sua mensagem.",
                parse_mode="Markdown",
            )
            return

        # Step 3: Store pending batch in user_data and ask for confirmation
        batch_id = uuid.uuid4().hex[:10]
        pending_batches: Dict[str, List[CardMatchResult]] = context.user_data.setdefault(
            "pending_batches", {}
        )
        pending_batches[batch_id] = pipeline_result.match_results

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Confirmar", callback_data=f"confirm:{batch_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Cancelar", callback_data=f"cancel:{batch_id}"
                    ),
                ]
            ]
        )

        response_text = self.format_confirmation_message(pipeline_result)
        await status_msg.edit_text(
            response_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    async def handle_confirmation_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Process inline button callback (Confirmar / Cancelar)."""
        query = update.callback_query
        if query is None or not query.data:
            return
        if not await self._ensure_authorized(update):
            return

        await query.answer()
        action, _, batch_id = query.data.partition(":")
        pending_batches: Dict[str, List[CardMatchResult]] = context.user_data.get(
            "pending_batches", {}
        )
        matches = pending_batches.pop(batch_id, None)

        if matches is None:
            await query.edit_message_text(
                "⚠️ Esta solicitação de confirmação já expirou ou já foi processada."
            )
            return

        if action == "cancel":
            await query.edit_message_text("🚫 Registro cancelado pelo usuário.")
            return

        if action == "confirm":
            user = update.effective_user
            user_id = user.id if user else 0
            username = user.username if user else None

            try:
                total_copies = self._collection_repo.register_cards(
                    user_id=user_id,
                    username=username,
                    matches=matches,
                )
            except PersistenceError as exc:
                logger.exception("PersistenceError while confirming batch=%s: %s", batch_id, exc)
                await query.edit_message_text(f"❌ Erro ao salvar cartas confirmadas: {exc}")
                return

            confirmed_lines = [
                f"• {m.format_telegram_line()}" for m in matches if m.matched and m.card_code
            ]
            unmatched_lines = [
                f"• {m.format_telegram_line()}" for m in matches if not m.matched
            ]

            summary_parts = [
                f"✅ *Registro Confirmado!* ({total_copies} cópia(s) adicionada(s))\n",
                *confirmed_lines,
            ]
            if unmatched_lines:
                summary_parts.append("\n⚠️ *Ignoradas (não encontradas):*")
                summary_parts.extend(unmatched_lines)

            await query.edit_message_text(
                "\n".join(summary_parts),
                parse_mode="Markdown",
            )

    async def global_error_handler(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Log any unhandled exception raised during Telegram update polling or dispatching."""
        logger.error(
            "Unhandled exception in Telegram Bot dispatcher (update=%r): %s",
            update,
            context.error,
            exc_info=context.error,
        )


def build_telegram_application(
    bot_token: str,
    controller: MiskatonicTelegramBot,
    connect_timeout: float = 30.0,
    read_timeout: float = 60.0,
) -> Application:
    """Create and wire the python-telegram-bot v20+ Application instance with extended timeouts."""
    if not bot_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is required to start the Telegram bot. Check your .env file."
        )

    application = (
        ApplicationBuilder()
        .token(bot_token)
        .connect_timeout(connect_timeout)
        .read_timeout(read_timeout)
        .write_timeout(read_timeout)
        .pool_timeout(connect_timeout)
        .get_updates_connect_timeout(connect_timeout)
        .get_updates_read_timeout(max(45.0, read_timeout))
        .build()
    )
    application.add_handler(CommandHandler(["start", "help"], controller.start_command))
    application.add_handler(
        CommandHandler(["colecao", "collection"], controller.collection_command)
    )
    application.add_handler(
        CommandHandler(["atualizar_cartas", "sync_cards"], controller.sync_cards_command)
    )
    application.add_handler(
        CommandHandler(["logs", "log"], controller.logs_command)
    )
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, controller.handle_voice_or_audio)
    )
    application.add_handler(
        CallbackQueryHandler(
            controller.handle_confirmation_callback,
            pattern=r"^(confirm|cancel):[a-f0-9]+$",
        )
    )
    application.add_error_handler(controller.global_error_handler)
    return application
