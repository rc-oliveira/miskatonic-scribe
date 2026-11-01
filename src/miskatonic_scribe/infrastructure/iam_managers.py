"""Singleton state managers for JSON-backed IAM (users.json) and dynamic configuration (config.json)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from miskatonic_scribe.domain.exceptions import PersistenceError

logger = logging.getLogger(__name__)

VALID_USER_STATUSES = frozenset({"pending", "approved", "rejected"})
VALID_USER_ROLES = frozenset({"user", "admin"})


class UserManager:
    """Async-safe Singleton manager encapsulating users.json I/O and in-memory IAM state."""

    _instance: Optional[UserManager] = None

    def __init__(self, file_path: Path | str = Path("users.json")) -> None:
        self._file_path = Path(file_path)
        self._lock = asyncio.Lock()
        self._users: Dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._load_sync_if_exists()

    @classmethod
    def get_instance(cls, file_path: Path | str | None = None) -> UserManager:
        """Return the singleton UserManager instance (updating file_path if explicitly provided)."""
        if cls._instance is None:
            cls._instance = cls(file_path=file_path or Path("users.json"))
        elif file_path is not None and Path(file_path) != cls._instance._file_path:
            cls._instance = cls(file_path=file_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (primarily for isolated testing)."""
        cls._instance = None

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _load_sync_if_exists(self) -> None:
        """Initial synchronous warm-up from disk when instantiated."""
        if not self._file_path.exists():
            self._users = {}
            self._loaded = True
            return
        try:
            raw_text = self._file_path.read_text(encoding="utf-8").strip()
            if not raw_text:
                self._users = {}
            else:
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    self._users = {
                        str(k): dict(v) for k, v in data.items() if isinstance(v, dict)
                    }
                else:
                    self._users = {}
            self._loaded = True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to warm up users.json from '%s': %s", self._file_path, exc)
            self._users = {}

    def _write_Unlocked(self) -> None:
        """Atomically persist in-memory users state to disk (caller MUST hold self._lock)."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._users, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._file_path)
        except OSError as exc:
            logger.error("Failed to write users file '%s': %s", self._file_path, exc)
            raise PersistenceError(f"Erro ao gravar arquivo de usuários '{self._file_path}': {exc}") from exc

    async def load(self) -> Dict[str, dict[str, Any]]:
        """Read users.json into memory under asyncio.Lock()."""
        async with self._lock:
            self._load_sync_if_exists()
            return {k: dict(v) for k, v in self._users.items()}

    async def save(self) -> None:
        """Persist current in-memory state to users.json under asyncio.Lock()."""
        async with self._lock:
            self._write_Unlocked()

    def get_user(self, telegram_id: int | str | None) -> Optional[dict[str, Any]]:
        """Return user record for telegram_id from memory, or None if not registered."""
        if telegram_id is None:
            return None
        if not self._loaded:
            self._load_sync_if_exists()
        record = self._users.get(str(telegram_id))
        return dict(record) if record is not None else None

    def is_approved(self, telegram_id: int | str | None) -> bool:
        """Return True iff telegram_id exists in UserManager and has status == 'approved'."""
        user = self.get_user(telegram_id)
        return bool(user and user.get("status") == "approved")

    def is_admin(self, telegram_id: int | str | None) -> bool:
        """Return True iff telegram_id has status == 'approved' and role == 'admin'."""
        user = self.get_user(telegram_id)
        return bool(
            user and user.get("status") == "approved" and user.get("role") == "admin"
        )

    def get_admin_ids(self) -> List[int]:
        """Return list of numeric Telegram IDs for all users with role == 'admin'."""
        if not self._loaded:
            self._load_sync_if_exists()
        admin_ids: List[int] = []
        for tid_str, data in self._users.items():
            if data.get("role") == "admin" and data.get("status") == "approved":
                try:
                    admin_ids.append(int(tid_str))
                except ValueError:
                    continue
        return admin_ids

    def get_users_by_status(self, status: str) -> Dict[str, dict[str, Any]]:
        """Return all users matching the given status ('pending', 'approved', 'rejected')."""
        if not self._loaded:
            self._load_sync_if_exists()
        return {
            tid: dict(data)
            for tid, data in self._users.items()
            if data.get("status") == status
        }

    def get_all_users(self) -> Dict[str, dict[str, Any]]:
        """Return a shallow copy of all user records."""
        if not self._loaded:
            self._load_sync_if_exists()
        return {tid: dict(data) for tid, data in self._users.items()}

    async def request_invite(
        self,
        telegram_id: int | str,
        first_name: str,
        username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record or update a user in users.json with status = 'pending' under asyncio.Lock()."""
        tid = str(telegram_id).strip()
        if not tid:
            raise ValueError("telegram_id must be non-empty.")

        async with self._lock:
            self._load_sync_if_exists()
            existing = self._users.get(tid, {})
            now_iso = datetime.now(timezone.utc).isoformat()
            record: dict[str, Any] = {
                "first_name": (first_name or existing.get("first_name") or "Investigador").strip(),
                "username": username if username is not None else existing.get("username"),
                "status": "pending",
                "role": existing.get("role") if existing.get("role") in VALID_USER_ROLES else "user",
                "created_at": existing.get("created_at") or now_iso,
            }
            self._users[tid] = record
            self._write_Unlocked()
            logger.info("Registered pending invite for telegram_id=%s (%s).", tid, record["first_name"])
            return dict(record)

    async def set_user_status(
        self,
        telegram_id: int | str,
        status: str,
    ) -> Optional[dict[str, Any]]:
        """Update a user's status ('pending', 'approved', 'rejected') in users.json under asyncio.Lock()."""
        if status not in VALID_USER_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Expected one of {sorted(VALID_USER_STATUSES)}.")

        tid = str(telegram_id).strip()
        async with self._lock:
            self._load_sync_if_exists()
            if tid not in self._users:
                return None
            self._users[tid]["status"] = status
            self._write_Unlocked()
            logger.info("Updated status for telegram_id=%s to '%s'.", tid, status)
            return dict(self._users[tid])

    async def seed_admin(
        self,
        telegram_id: int | str,
        first_name: str,
        username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Insert or update an administrator in users.json with status='approved' and role='admin'."""
        tid = str(telegram_id).strip()
        if not tid:
            raise ValueError("telegram_id must be non-empty.")

        async with self._lock:
            self._load_sync_if_exists()
            existing = self._users.get(tid, {})
            now_iso = datetime.now(timezone.utc).isoformat()
            record: dict[str, Any] = {
                "first_name": (first_name or "Admin").strip(),
                "username": username if username is not None else existing.get("username"),
                "status": "approved",
                "role": "admin",
                "created_at": existing.get("created_at") or now_iso,
            }
            self._users[tid] = record
            self._write_Unlocked()
            logger.info("Seeded admin user telegram_id=%s (%s) in '%s'.", tid, record["first_name"], self._file_path)
            return dict(record)


class ConfigManager:
    """Async-safe Singleton manager encapsulating config.json I/O and in-memory dynamic settings."""

    _instance: Optional[ConfigManager] = None

    def __init__(
        self,
        file_path: Path | str = Path("config.json"),
        defaults: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._file_path = Path(file_path)
        self._lock = asyncio.Lock()
        self._defaults: Dict[str, Any] = dict(defaults) if defaults else {
            "transcription_mode": "whisper",
            "whisper_model_name": "base",
            "fuzzy_match_threshold": 65,
            "arkhamdb_refresh_days": 30,
        }
        self._config: Dict[str, Any] = dict(self._defaults)
        self._loaded = False
        self._load_sync_if_exists()

    @classmethod
    def get_instance(
        cls,
        file_path: Path | str | None = None,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> ConfigManager:
        """Return the singleton ConfigManager instance."""
        if cls._instance is None:
            cls._instance = cls(
                file_path=file_path or Path("config.json"),
                defaults=defaults,
            )
        elif file_path is not None and Path(file_path) != cls._instance._file_path:
            cls._instance = cls(file_path=file_path, defaults=defaults)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (primarily for isolated testing)."""
        cls._instance = None

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _load_sync_if_exists(self) -> None:
        """Warm up dynamic config from disk if config.json exists."""
        merged = dict(self._defaults)
        if self._file_path.exists():
            try:
                raw_text = self._file_path.read_text(encoding="utf-8").strip()
                if raw_text:
                    data = json.loads(raw_text)
                    if isinstance(data, dict):
                        merged.update(data)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read config.json from '%s': %s", self._file_path, exc)
        self._config = merged
        self._loaded = True

    def _write_Unlocked(self) -> None:
        """Atomically persist in-memory config to disk (caller MUST hold self._lock)."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._file_path)
        except OSError as exc:
            logger.error("Failed to write config file '%s': %s", self._file_path, exc)
            raise PersistenceError(f"Erro ao gravar arquivo de configuração '{self._file_path}': {exc}") from exc

    async def load(self) -> Dict[str, Any]:
        """Load config.json into memory under asyncio.Lock()."""
        async with self._lock:
            self._load_sync_if_exists()
            return dict(self._config)

    async def save(self) -> None:
        """Persist in-memory configuration to config.json under asyncio.Lock()."""
        async with self._lock:
            self._write_Unlocked()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value from in-memory state."""
        if not self._loaded:
            self._load_sync_if_exists()
        return self._config.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        """Update a configuration key in memory and persist to config.json under asyncio.Lock()."""
        async with self._lock:
            self._load_sync_if_exists()
            self._config[key] = value
            self._write_Unlocked()

    def get_all(self) -> Dict[str, Any]:
        """Return a shallow copy of the current configuration."""
        if not self._loaded:
            self._load_sync_if_exists()
        return dict(self._config)
