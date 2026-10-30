"""Local JSON repository for persisting confirmed user card collections."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from miskatonic_scribe.domain.exceptions import PersistenceError
from miskatonic_scribe.domain.interfaces import IUserCollectionRepository
from miskatonic_scribe.domain.models import CardMatchResult

logger = logging.getLogger(__name__)


class JsonUserCollectionRepository(IUserCollectionRepository):
    """Stores confirmed card registrations per Telegram user in a local JSON file."""

    def __init__(self, db_path: Path = Path("data/user_collections.json")) -> None:
        self._db_path = db_path

    def _read_db(self) -> Dict[str, Any]:
        if not self._db_path.exists():
            return {"users": {}}
        try:
            with self._db_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "users" in data:
                    return data
                return {"users": {}}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to read user collection DB '%s': %s", self._db_path, exc)
            raise PersistenceError(f"Erro ao ler banco de coleções: {exc}") from exc

    def _write_db(self, data: Dict[str, Any]) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._db_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Failed to write user collection DB '%s': %s", self._db_path, exc)
            raise PersistenceError(f"Erro ao salvar banco de coleções: {exc}") from exc

    def register_cards(
        self,
        user_id: int,
        username: str | None,
        matches: Sequence[CardMatchResult],
    ) -> int:
        """Persist matched cards for a user and return the total copies registered in this batch."""
        valid_matches = [m for m in matches if m.matched and m.card_code]
        if not valid_matches:
            return 0

        db = self._read_db()
        user_key = str(user_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        user_entry = db["users"].setdefault(
            user_key,
            {
                "user_id": user_id,
                "username": username,
                "updated_at": now_iso,
                "cards": {},
            },
        )
        user_entry["username"] = username or user_entry.get("username")
        user_entry["updated_at"] = now_iso

        cards_map: Dict[str, Any] = user_entry.setdefault("cards", {})
        added_copies = 0

        for match in valid_matches:
            code = str(match.card_code)
            qty = match.intent.quantidade
            added_copies += qty

            if code in cards_map:
                cards_map[code]["quantity"] += qty
                cards_map[code]["updated_at"] = now_iso
            else:
                cards_map[code] = {
                    "code": code,
                    "name": match.official_name,
                    "xp": match.matched_xp,
                    "subname": match.subname,
                    "quantity": qty,
                    "updated_at": now_iso,
                }

        self._write_db(db)
        logger.info(
            "Registered %d card copies (%d unique cards) for user_id=%s.",
            added_copies,
            len(valid_matches),
            user_id,
        )
        return added_copies

    def get_user_collection(self, user_id: int) -> List[dict]:
        """Return sorted list of cards registered for a given user."""
        db = self._read_db()
        user_entry = db.get("users", {}).get(str(user_id), {})
        cards_map = user_entry.get("cards", {})
        cards_list = list(cards_map.values())
        cards_list.sort(key=lambda c: (str(c.get("name", "")), int(c.get("xp", 0)), str(c.get("code", ""))))
        return cards_list
