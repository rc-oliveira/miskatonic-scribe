"""ArkhamDB card repository with automatic local cache download and periodic TTL refresh."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

import requests

from miskatonic_scribe.domain.exceptions import CardDatabaseError
from miskatonic_scribe.domain.interfaces import ICardRepository
from miskatonic_scribe.domain.models import ArkhamCardRecord

logger = logging.getLogger(__name__)


class ArkhamDBCardRepository(ICardRepository):
    """Loads Arkham Horror LCG cards from local cards.json, downloading or refreshing from ArkhamDB."""

    def __init__(
        self,
        cards_path: Path = Path("cards.json"),
        api_url: str = "https://arkhamdb.com/api/public/cards/",
        refresh_days: int = 30,
        http_get: Callable[..., requests.Response] = requests.get,
        request_timeout: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cards_path = cards_path
        self._api_url = api_url
        self._refresh_days = max(1, refresh_days)
        self._http_get = http_get
        self._request_timeout = request_timeout
        self._clock = clock
        self._cached_cards: Optional[List[ArkhamCardRecord]] = None
        self._last_checked_at: float = 0.0

    def _is_file_expired(self) -> bool:
        """Return True if cards.json is older than refresh_days."""
        if not self._cards_path.exists():
            return True
        try:
            age_seconds = self._clock() - self._cards_path.stat().st_mtime
            return age_seconds >= (self._refresh_days * 86400)
        except OSError:
            return True

    def _download_from_arkhamdb(self) -> int:
        """Download cards payload from ArkhamDB and write atomically to cards_path."""
        logger.info("Baixando base de cartas do ArkhamDB (%s)...", self._api_url)
        try:
            self._cards_path.parent.mkdir(parents=True, exist_ok=True)
            response = self._http_get(self._api_url, timeout=self._request_timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise CardDatabaseError("ArkhamDB API did not return a JSON list of cards.")

            tmp_path = self._cards_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._cards_path)
        except requests.RequestException as exc:
            logger.error("Network error while downloading ArkhamDB cards: %s", exc)
            raise CardDatabaseError(
                f"Falha ao baixar base de cartas do ArkhamDB ({self._api_url}): {exc}"
            ) from exc
        except (ValueError, OSError) as exc:
            logger.error("Failed to save or parse ArkhamDB response: %s", exc)
            raise CardDatabaseError(
                f"Erro ao processar ou salvar '{self._cards_path}': {exc}"
            ) from exc
        else:
            logger.info("'%s' atualizado com sucesso (%d entradas).", self._cards_path, len(payload))
            return len(payload)

    def ensure_cards_file(self, force: bool = False) -> Path:
        """Ensure cards.json exists and is fresh (within refresh_days), downloading if needed."""
        file_exists = self._cards_path.exists()
        if force or not file_exists:
            self._download_from_arkhamdb()
            self._last_checked_at = self._clock()
            return self._cards_path

        if self._is_file_expired():
            logger.info(
                "Local '%s' exceeded TTL of %d days. Refreshing from ArkhamDB...",
                self._cards_path,
                self._refresh_days,
            )
            try:
                self._download_from_arkhamdb()
                self._cached_cards = None
            except CardDatabaseError as exc:
                logger.warning(
                    "Periodic refresh failed (%s). Falling back to existing local '%s'.",
                    exc,
                    self._cards_path,
                )
            finally:
                self._last_checked_at = self._clock()

        return self._cards_path

    @staticmethod
    def _to_record(raw: dict[str, Any]) -> Optional[ArkhamCardRecord]:
        """Convert a raw ArkhamDB JSON object into an ArkhamCardRecord (player cards and investigators only)."""
        code = str(raw.get("code", "")).strip()
        real_name = str(raw.get("real_name") or raw.get("name") or "").strip()
        name = str(raw.get("name") or real_name).strip()

        if not code or not real_name:
            return None

        # Exclude scenario/encounter cards (e.g. mythos treacheries like "You've had worse...")
        if raw.get("encounter_code") or str(raw.get("faction_code", "")).lower() == "mythos":
            return None
        type_code = str(raw.get("type_code", "")).lower()
        if type_code in {"scenario", "agenda", "act", "location", "enemy"}:
            return None

        raw_xp = raw.get("xp", 0)
        try:
            xp = int(raw_xp) if raw_xp is not None else 0
        except (ValueError, TypeError):
            xp = 0

        subname = raw.get("real_subname") or raw.get("subname")
        subname_str = str(subname).strip() if subname else None

        duplicate_of = raw.get("duplicate_of_code") or raw.get("duplicate_of")
        duplicate_str = str(duplicate_of).strip() if duplicate_of else None

        octgn_id = raw.get("octgn_id")
        octgn_str = str(octgn_id).strip() if octgn_id else None

        return ArkhamCardRecord(
            code=code,
            real_name=real_name,
            name=name,
            xp=xp,
            subname=subname_str,
            pack_code=raw.get("pack_code"),
            faction_code=raw.get("faction_code"),
            type_code=raw.get("type_code"),
            duplicate_of_code=duplicate_str,
            octgn_id=octgn_str,
        )

    def load_cards(self, force_reload: bool = False) -> Sequence[ArkhamCardRecord]:
        """Load and cache card records from cards.json (auto-refreshing if TTL expired)."""
        if self._cached_cards is not None and not force_reload and not self._is_file_expired():
            return self._cached_cards

        self.ensure_cards_file(force=force_reload)

        try:
            with self._cards_path.open("r", encoding="utf-8") as f:
                raw_list = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to read or parse '%s': %s", self._cards_path, exc)
            raise CardDatabaseError(f"Arquivo '{self._cards_path}' inválido ou corrompido: {exc}") from exc

        if not isinstance(raw_list, list):
            raise CardDatabaseError(f"Expected list in '{self._cards_path}', got {type(raw_list).__name__}.")

        records: List[ArkhamCardRecord] = []
        for item in raw_list:
            if isinstance(item, dict):
                record = self._to_record(item)
                if record is not None:
                    records.append(record)

        self._cached_cards = records
        logger.info("Loaded %d valid card records from '%s'.", len(records), self._cards_path)
        return self._cached_cards

    def refresh_cards(self) -> Sequence[ArkhamCardRecord]:
        """Force an immediate download from ArkhamDB and reload the in-memory card cache."""
        return self.load_cards(force_reload=True)
