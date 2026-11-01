"""Telegram Bot v20+ handlers for voice, audio, text messages, IAM onboarding, Admin Panel, and .o8d export."""

from __future__ import annotations

import asyncio
import functools
import inspect
import io
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

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
from miskatonic_scribe.domain.models import (
    CardMatchResult,
    InvestigatorMatchResult,
    PipelineExecutionResult,
)
from miskatonic_scribe.infrastructure.arkhamdb_deck_exporter import ArkhamDBDeckExporter
from miskatonic_scribe.infrastructure.iam_managers import ConfigManager, UserManager
from miskatonic_scribe.services.registration_pipeline import CardRegistrationPipeline

logger = logging.getLogger(__name__)

PendingBatchItem = Union[PipelineExecutionResult, List[CardMatchResult]]


def require_approved(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that intercepts messages/callbacks and verifies status == 'approved' in UserManager."""

    @functools.wraps(func)
    async def wrapper(
        self: MiskatonicTelegramBot,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        await self._user_manager.load()
        user = update.effective_user
        user_id = user.id if user else None
        username = getattr(user, "username", None) if user else None

        if self._user_manager.is_approved(user_id):
            return await func(self, update, context, *args, **kwargs)

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
                "⛔ Acesso negado. Este sistema é fechado. Utilize /convite para solicitar acesso."
            )
        return None

    return wrapper


def require_admin(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that intercepts admin commands/callbacks and verifies role == 'admin' and status == 'approved'."""

    @functools.wraps(func)
    async def wrapper(
        self: MiskatonicTelegramBot,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        await self._user_manager.load()
        user = update.effective_user
        user_id = user.id if user else None
        username = getattr(user, "username", None) if user else None

        if self._user_manager.is_admin(user_id):
            return await func(self, update, context, *args, **kwargs)

        logger.warning(
            "Unauthorized admin access attempt blocked for user_id=%s (username=%s).",
            user_id,
            username,
        )
        if update.callback_query:
            await update.callback_query.answer(
                "⛔ Acesso restrito a administradores.",
                show_alert=True,
            )
        elif update.message:
            await update.message.reply_text(
                "⛔ Acesso negado. Este comando é exclusivo para administradores."
            )
        return None

    return wrapper


class MiskatonicTelegramBot:
    """Telegram Bot controller encapsulating IAM onboarding, Admin Panel, voice, text, and .o8d export handlers."""

    def __init__(
        self,
        audio_storage: IAudioStorageManager,
        pipeline: CardRegistrationPipeline,
        collection_repo: IUserCollectionRepository,
        user_manager: Optional[UserManager] = None,
        config_manager: Optional[ConfigManager] = None,
        card_repo: Optional[ICardRepository] = None,
        deck_exporter: Optional[ArkhamDBDeckExporter] = None,
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
        self._user_manager = user_manager or UserManager.get_instance()
        self._config_manager = config_manager or ConfigManager.get_instance()
        self._card_repo = card_repo
        self._deck_exporter = deck_exporter or ArkhamDBDeckExporter()
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._download_retries = max(1, download_retries)
        self._log_file_path = log_file_path
        self._use_thread_pool = use_thread_pool
        self._sleep_fn = sleep_fn

    def is_user_authorized(self, user_id: Optional[int]) -> bool:
        """Return True only if user_id has status == 'approved' in UserManager."""
        return self._user_manager.is_approved(user_id)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help: check UserManager and guide unapproved users to /convite."""
        if not update.message:
            return

        await self._user_manager.load()
        user = update.effective_user
        user_id = user.id if user else None
        user_record = self._user_manager.get_user(user_id)

        if user_record is None or user_record.get("status") != "approved":
            status = user_record.get("status") if user_record else None
            if status == "pending":
                await update.message.reply_text(
                    "🔒 *Sistema Fechado*\n\n"
                    "Sua solicitação já está pendente de aprovação pelos administradores. "
                    "Você será notificado assim que seu acesso for liberado.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "🔒 *Sistema Fechado*\n\n"
                    "Este bot é de acesso restrito. Para solicitar autorização de uso, "
                    "envie o comando `/convite`.",
                    parse_mode="Markdown",
                )
            return

        admin_line = (
            "\n• `/admin` - Abre o Painel de Administração de usuários"
            if user_record.get("role") == "admin"
            else ""
        )
        welcome_text = (
            "🐙 *Miskatonic Scribe - Arkham Horror LCG*\n\n"
            "Envie uma *mensagem de voz*, *arquivo de áudio* ou *mensagem de texto* dizendo "
            "o investigador e as cartas (em Português ou Inglês), seus níveis (XP) e quantidades.\n\n"
            "Exemplo:\n"
            "_'Deck da Diana Stanley: duas cópias de Definhar nível 3 e dois Foi só um arranhão'_\n\n"
            "💡 Enquanto uma lista estiver aguardando confirmação, você também pode enviar um texto "
            "(ex: _'trocar Astral Travel por 2 Foi só um arranhão'_) para corrigir o lote!\n\n"
            "Comandos disponíveis:\n"
            "• `/start` - Exibe esta mensagem de ajuda\n"
            "• `/colecao` - Lista as cartas já confirmadas e salvas na sua coleção\n"
            "• `/atualizar_cartas` - Força o download imediato da base do ArkhamDB\n"
            f"• `/logs` - Exibe as últimas linhas do arquivo de log do servidor{admin_line}"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")

    async def invite_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /convite: register user as 'pending' in users.json and notify all admins."""
        if not update.message or not update.effective_user:
            return

        await self._user_manager.load()
        user = update.effective_user
        user_id = user.id
        first_name = getattr(user, "first_name", None) or getattr(user, "username", None) or "Investigador"
        username = getattr(user, "username", None)

        if self._user_manager.is_approved(user_id):
            await update.message.reply_text("✅ Seu usuário já está aprovado no sistema!")
            return

        record = await self._user_manager.request_invite(
            telegram_id=user_id,
            first_name=first_name,
            username=username,
        )
        await update.message.reply_text("Solicitação enviada.")

        # Trigger notification to all admins in users.json
        admin_ids = self._user_manager.get_admin_ids()
        username_label = f"@{username}" if username else "N/A"
        notification_text = (
            "📩 *Nova Solicitação de Acesso (/convite)*\n\n"
            f"• *Nome:* {record['first_name']}\n"
            f"• *Username:* {username_label}\n"
            f"• *Telegram ID:* `{user_id}`\n"
            f"• *Data:* {record['created_at']}"
        )
        approval_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Aprovar", callback_data=f"approve_{user_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Rejeitar", callback_data=f"reject_{user_id}"
                    ),
                ]
            ]
        )

        bot_obj = getattr(context, "bot", None)
        send_msg = getattr(bot_obj, "send_message", None)
        if callable(send_msg):
            for admin_id in admin_ids:
                try:
                    res = send_msg(
                        chat_id=admin_id,
                        text=notification_text,
                        parse_mode="Markdown",
                        reply_markup=approval_keyboard,
                    )
                    if inspect.isawaitable(res):
                        await res
                except Exception as exc:
                    logger.warning("Failed to notify admin_id=%s about invite from %s: %s", admin_id, user_id, exc)

    @require_admin
    async def handle_approval_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle approve_<id> and reject_<id> inline button callbacks from admins."""
        query = update.callback_query
        if query is None or not query.data:
            return

        await query.answer()
        action, _, target_id_str = query.data.partition("_")
        new_status = "approved" if action == "approve" else "rejected"

        updated_user = await self._user_manager.set_user_status(target_id_str, new_status)
        if updated_user is None:
            await query.edit_message_text(
                f"⚠️ Usuário `{target_id_str}` não encontrado em `users.json`.",
                parse_mode="Markdown",
            )
            return

        name_display = updated_user.get("first_name") or target_id_str
        if new_status == "approved":
            admin_feedback = f"✅ Acesso de *{name_display}* (ID: `{target_id_str}`) *aprovado* com sucesso."
            user_notification = (
                "🎉 *Acesso Aprovado!*\n\n"
                "Sua solicitação no *Miskatonic Scribe* foi aprovada por um administrador. "
                "Envie `/start` para começar!"
            )
        else:
            admin_feedback = f"🚫 Acesso de *{name_display}* (ID: `{target_id_str}`) *rejeitado*."
            user_notification = (
                "🚫 *Solicitação Recusada*\n\n"
                "Sua solicitação de acesso ao *Miskatonic Scribe* não foi aprovada."
            )

        await query.edit_message_text(admin_feedback, parse_mode="Markdown")

        bot_obj = getattr(context, "bot", None)
        send_msg = getattr(bot_obj, "send_message", None)
        if callable(send_msg):
            try:
                res = send_msg(
                    chat_id=int(target_id_str),
                    text=user_notification,
                    parse_mode="Markdown",
                )
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Could not notify user %s of status change to %s: %s", target_id_str, new_status, exc)

    @staticmethod
    def _build_admin_home_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⏳ Listar Pendentes", callback_data="admin:list_pending"
                    ),
                    InlineKeyboardButton(
                        "✅ Listar Aprovados", callback_data="admin:list_approved"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Revogar Acessos", callback_data="admin:revoke_menu"
                    ),
                ],
            ]
        )

    @require_admin
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /admin command to display the inline Admin Panel."""
        if not update.message:
            return

        pending_count = len(self._user_manager.get_users_by_status("pending"))
        approved_count = len(self._user_manager.get_users_by_status("approved"))
        text = (
            "🛡️ *Painel de Administração — Miskatonic Scribe*\n\n"
            f"• *Usuários Aprovados:* {approved_count}\n"
            f"• *Solicitações Pendentes:* {pending_count}\n\n"
            "Selecione uma opção abaixo para gerenciar os acessos:"
        )
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=self._build_admin_home_keyboard(),
        )

    @require_admin
    async def handle_admin_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle inline navigation callbacks inside the /admin panel (admin:...)."""
        query = update.callback_query
        if query is None or not query.data:
            return

        await query.answer()
        _, _, subaction = query.data.partition(":")

        if subaction == "home":
            pending_count = len(self._user_manager.get_users_by_status("pending"))
            approved_count = len(self._user_manager.get_users_by_status("approved"))
            text = (
                "🛡️ *Painel de Administração — Miskatonic Scribe*\n\n"
                f"• *Usuários Aprovados:* {approved_count}\n"
                f"• *Solicitações Pendentes:* {pending_count}\n\n"
                "Selecione uma opção abaixo para gerenciar os acessos:"
            )
            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=self._build_admin_home_keyboard(),
            )
            return

        if subaction == "list_pending":
            pending_users = self._user_manager.get_users_by_status("pending")
            lines = ["⏳ *Usuários Pendentes:*\n"]
            buttons: List[List[InlineKeyboardButton]] = []

            if not pending_users:
                lines.append("_Nenhuma solicitação pendente no momento._")
            else:
                for tid, info in pending_users.items():
                    fname = info.get("first_name") or "Investigador"
                    uname = f" (@{info['username']})" if info.get("username") else ""
                    lines.append(f"• *{fname}*{uname} — ID: `{tid}`")
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                f"✅ Aprovar {fname}", callback_data=f"approve_{tid}"
                            ),
                            InlineKeyboardButton(
                                f"❌ Rejeitar {fname}", callback_data=f"reject_{tid}"
                            ),
                        ]
                    )

            buttons.append([InlineKeyboardButton("🔙 Voltar", callback_data="admin:home")])
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if subaction == "list_approved":
            approved_users = self._user_manager.get_users_by_status("approved")
            lines = ["✅ *Usuários Aprovados:*\n"]
            if not approved_users:
                lines.append("_Nenhum usuário aprovado._")
            else:
                for tid, info in approved_users.items():
                    fname = info.get("first_name") or "Investigador"
                    uname = f" (@{info['username']})" if info.get("username") else ""
                    role = info.get("role", "user")
                    lines.append(f"• *{fname}*{uname} — ID: `{tid}` [{role}]")

            buttons = [[InlineKeyboardButton("🔙 Voltar", callback_data="admin:home")]]
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if subaction == "revoke_menu":
            approved_users = self._user_manager.get_users_by_status("approved")
            lines = ["🚫 *Revogar Acesso de Usuário:*\n"]
            buttons = []

            for tid, info in approved_users.items():
                fname = info.get("first_name") or "Investigador"
                role = info.get("role", "user")
                lines.append(f"• *{fname}* — ID: `{tid}` [{role}]")
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🚫 Revogar {fname} ({tid})",
                            callback_data=f"admin:revoke_{tid}",
                        )
                    ]
                )

            if not buttons:
                lines.append("_Nenhum usuário aprovado disponível para revogação._")

            buttons.append([InlineKeyboardButton("🔙 Voltar", callback_data="admin:home")])
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if subaction.startswith("revoke_"):
            target_tid = subaction.removeprefix("revoke_")
            updated = await self._user_manager.set_user_status(target_tid, "rejected")
            buttons = [[InlineKeyboardButton("🔙 Voltar ao Painel", callback_data="admin:home")]]
            if updated is None:
                await query.edit_message_text(
                    f"⚠️ Usuário `{target_tid}` não encontrado.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

            fname = updated.get("first_name") or target_tid
            await query.edit_message_text(
                f"🚫 Acesso do usuário *{fname}* (ID: `{target_tid}`) foi *revogado* (`status = rejected`).",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

    @require_approved
    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /logs command to return the tail of the persistent log file."""
        if not update.message:
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

    @require_approved
    async def sync_cards_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /atualizar_cartas (/sync_cards) to force-refresh cards.json from ArkhamDB."""
        if not update.message:
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

    @require_approved
    async def collection_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /colecao command to display the user's persisted cards."""
        if not update.message or not update.effective_user:
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
                "📭 Sua coleção ainda está vazia. Envie um áudio ou texto para registrar cartas!"
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
        """Build the Telegram response listing understood transcription, investigator, and matched cards."""
        lines: List[str] = [
            f"🎙️ *Entrada:* _{result.transcribed_text}_\n",
        ]
        if result.investigator_match is not None:
            lines.append(f"{result.investigator_match.format_telegram_line()}\n")

        total_copies = sum(m.intent.quantidade for m in result.match_results if m.matched)
        lines.append(
            f"🃏 *Cartas Identificadas ({total_copies} cópia(s) / {len(result.match_results)} entrada(s)):*"
        )
        for match in result.match_results:
            lines.append(f"• {match.format_telegram_line()}")

        lines.append(
            "\n💡 _Envie uma mensagem de texto se quiser adicionar, remover ou trocar alguma carta antes de confirmar._"
        )
        lines.append("Deseja confirmar o registro destas cartas?")
        return "\n".join(lines)

    @staticmethod
    def _build_confirmation_keyboard(batch_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
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

    @require_approved
    async def handle_voice_or_audio(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Download voice/audio .ogg file, run pipeline, and ask user for confirmation."""
        if not update.message:
            return

        user_id = update.effective_user.id if update.effective_user else None
        attachment = update.message.voice or update.message.audio
        if attachment is None:
            await update.message.reply_text("⚠️ Nenhum arquivo de voz ou áudio encontrado na mensagem.")
            return

        status_msg = await update.message.reply_text(
            "⏳ Baixando áudio e consultando os arquivos da Universidade Miskatonic..."
        )

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

        if not pipeline_result.match_results and pipeline_result.investigator_match is None:
            await status_msg.edit_text(
                f"🎙️ *Transcrição:* _{pipeline_result.transcribed_text}_\n\n"
                "⚠️ Nenhuma carta de Arkham Horror LCG foi identificada na sua mensagem.",
                parse_mode="Markdown",
            )
            return

        batch_id = uuid.uuid4().hex[:10]
        pending_batches: Dict[str, PendingBatchItem] = context.user_data.setdefault(
            "pending_batches", {}
        )
        pending_batches[batch_id] = pipeline_result
        context.user_data["active_batch_id"] = batch_id

        response_text = self.format_confirmation_message(pipeline_result)
        await status_msg.edit_text(
            response_text,
            parse_mode="Markdown",
            reply_markup=self._build_confirmation_keyboard(batch_id),
        )

    @require_approved
    async def handle_text_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Process text messages to either edit an active pending batch or create a new card batch."""
        if not update.message or not update.message.text:
            return

        user_id = update.effective_user.id if update.effective_user else None
        raw_text = update.message.text.strip()
        if not raw_text:
            return

        status_msg = await update.message.reply_text("⏳ Processando mensagem de texto...")

        pending_batches: Dict[str, PendingBatchItem] = context.user_data.setdefault(
            "pending_batches", {}
        )
        active_batch_id: Optional[str] = context.user_data.get("active_batch_id")
        context_investigator: Optional[InvestigatorMatchResult] = None
        if active_batch_id and active_batch_id in pending_batches:
            existing_peek = pending_batches[active_batch_id]
            if isinstance(existing_peek, PipelineExecutionResult):
                context_investigator = existing_peek.investigator_match

        try:
            if self._use_thread_pool:
                delta_result = await asyncio.to_thread(
                    self._pipeline.process_text,
                    raw_text,
                    context_investigator,
                )
            else:
                delta_result = self._pipeline.process_text(raw_text, context_investigator)
        except LLMExtractionError as exc:
            logger.error("LLMExtractionError on text message for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"🤖 Falha ao processar texto via Gemini: {exc}")
            return
        except MiskatonicScribeError as exc:
            logger.error("MiskatonicScribeError on text message for user_id=%s: %s", user_id, exc, exc_info=True)
            await status_msg.edit_text(f"❌ Erro no processamento: {exc}")
            return
        except Exception as exc:
            logger.exception("Unexpected error in text pipeline for user_id=%s: %s", user_id, exc)
            await status_msg.edit_text(f"❌ Erro inesperado ao processar texto: {exc}")
            return

        if active_batch_id and active_batch_id in pending_batches:
            existing_item = pending_batches.pop(active_batch_id)
            if isinstance(existing_item, list):
                existing_result = PipelineExecutionResult(
                    transcribed_text="",
                    model_used=delta_result.model_used,
                    extracted_intents=[m.intent for m in existing_item],
                    match_results=existing_item,
                )
            else:
                existing_result = existing_item

            final_result = CardRegistrationPipeline.merge_pending_result(
                current=existing_result,
                delta=delta_result,
            )
        else:
            final_result = delta_result

        if not final_result.match_results and final_result.investigator_match is None:
            await status_msg.edit_text(
                "⚠️ Nenhuma carta ou investigador foi identificado na mensagem de texto."
            )
            return

        new_batch_id = uuid.uuid4().hex[:10]
        pending_batches[new_batch_id] = final_result
        context.user_data["active_batch_id"] = new_batch_id

        response_text = self.format_confirmation_message(final_result)
        await status_msg.edit_text(
            response_text,
            parse_mode="Markdown",
            reply_markup=self._build_confirmation_keyboard(new_batch_id),
        )

    @staticmethod
    def _unpack_pending_item(
        item: PendingBatchItem,
    ) -> tuple[List[CardMatchResult], Optional[InvestigatorMatchResult]]:
        if isinstance(item, PipelineExecutionResult):
            return item.match_results, item.investigator_match
        return list(item), None

    @require_approved
    async def handle_confirmation_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Process inline button callback (Confirmar / Cancelar) and export .o8d file for ArkhamDB."""
        query = update.callback_query
        if query is None or not query.data:
            return

        await query.answer()
        action, _, batch_id = query.data.partition(":")
        pending_batches: Dict[str, PendingBatchItem] = context.user_data.get(
            "pending_batches", {}
        )
        pending_item = pending_batches.pop(batch_id, None)
        if context.user_data.get("active_batch_id") == batch_id:
            context.user_data.pop("active_batch_id", None)

        if pending_item is None:
            await query.edit_message_text(
                "⚠️ Esta solicitação de confirmação já expirou ou já foi processada."
            )
            return

        if action == "cancel":
            await query.edit_message_text("🚫 Registro cancelado pelo usuário.")
            return

        if action == "confirm":
            matches, investigator = self._unpack_pending_item(pending_item)
            user = update.effective_user
            user_id = user.id if user else 0
            username = user.username if user else None

            try:
                total_copies = self._collection_repo.register_cards(
                    user_id=user_id,
                    username=username,
                    matches=matches,
                    investigator=investigator,
                )
            except TypeError:
                # Backward compatibility for custom repositories with 3-arg signature
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
            ]
            if investigator is not None:
                summary_parts.append(f"{investigator.format_telegram_line()}\n")
            summary_parts.extend(confirmed_lines)

            if unmatched_lines:
                summary_parts.append("\n⚠️ *Ignoradas (não encontradas):*")
                summary_parts.extend(unmatched_lines)

            inv_label = (
                investigator.official_name
                if (investigator and investigator.matched and investigator.official_name)
                else "Investigator"
            )
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            deck_name = f"Miskatonic Scribe - {inv_label} ({date_str})"

            await query.edit_message_text(
                "\n".join(summary_parts),
                parse_mode="Markdown",
            )

            # Generate and send .o8d (OCTGN XML) file for 1-click import on https://arkhamdb.com/decks
            if confirmed_lines and getattr(query, "message", None) is not None:
                reply_doc = getattr(query.message, "reply_document", None)
                if callable(reply_doc):
                    try:
                        dek_content = self._deck_exporter.generate_octgn_dek(
                            deck_name=deck_name,
                            matches=matches,
                            investigator=investigator,
                        )
                        slug = re.sub(r"[^a-z0-9]+", "_", inv_label.lower()).strip("_") or "arkham"
                        filename = f"deck_{slug}_{date_str}.o8d"
                        bio = io.BytesIO(dek_content.encode("utf-8"))
                        bio.name = filename
                        res = reply_doc(
                            document=bio,
                            filename=filename,
                            caption=(
                                "📦 Arquivo `.o8d` (OCTGN) gerado! Você pode importá-lo diretamente em "
                                "https://arkhamdb.com/decks clicando em *Import Deck*."
                            ),
                            parse_mode="Markdown",
                        )
                        if inspect.isawaitable(res):
                            await res
                    except Exception as exc:
                        logger.warning("Could not send .o8d attachment in Telegram: %s", exc)

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
    application.add_handler(CommandHandler("convite", controller.invite_command))
    application.add_handler(CommandHandler("admin", controller.admin_command))
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
        MessageHandler(filters.TEXT & ~filters.COMMAND, controller.handle_text_message)
    )
    application.add_handler(
        CallbackQueryHandler(
            controller.handle_approval_callback,
            pattern=r"^(approve|reject)_\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            controller.handle_admin_callback,
            pattern=r"^admin:.+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            controller.handle_confirmation_callback,
            pattern=r"^(confirm|cancel):[a-f0-9]+$",
        )
    )
    application.add_error_handler(controller.global_error_handler)
    return application
