"""ArkhamDB card repository with automatic local cache download."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

import requests

from miskatonic_scribe.domain.exceptions import CardDatabaseError
from miskatonic_scribe.domain.interfaces import ICardRepository
from miskatonic_scribe.domain.models import ArkhamCardRecord

logger = logging.getLogger(__name__)


class ArkhamDBCardRepository(ICardRepository):
    """Loads Arkham Horror LCG cards from local cards.json, downloading from ArkhamDB if absent."""

    def __init__(
        self,
        cards_path: Path = Path("cards.json"),
        api_url: str = "https://arkhamdb.com/api/public/cards/",
        http_get: Callable[..., requests.Response] = requests.get,
        request_timeout: float = 30.0,
    ) -> None:
        self._cards_path = cards_path
        self._api_url = api_url
        self._http_get = http_get
        self._request_timeout = request_timeout
        self._cached_cards: Optional[List[ArkhamCardRecord]] = None

    def ensure_cards_file(self) -> Path:
        """Download cards.json from ArkhamDB if it does not exist locally."""
        if not self._cards_path.exists():
            logger.info(
                "Local '%s' not found. Downloading card database from %s...",
                self._cards_path,
                self._api_url,
            )
            try:
                self._cards_path.parent.mkdir(parents=True, exist_ok=True)
                response = self._http_get(self._api_url, timeout=self._request_timeout)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise CardDatabaseError("ArkhamDB API did not return a JSON list of cards.")
                with self._cards_path.open("w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
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
                logger.info("'%s' updated successfully (%d entries).", self._cards_path, len(payload))
        return self._cards_path

    @staticmethod
    def _to_record(raw: dict[str, Any]) -> Optional[ArkhamCardRecord]:
        """Convert a raw ArkhamDB JSON object into an ArkhamCardRecord."""
        code = str(raw.get("code", "")).strip()
        real_name = str(raw.get("real_name") or raw.get("name") or "").strip()
        name = str(raw.get("name") or real_name).strip()

        if not code or not real_name:
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
        )

    def load_cards(self, force_reload: bool = False) -> Sequence[ArkhamCardRecord]:
        """Load and cache card records from cards.json."""
        if self._cached_cards is not None and not force_reload:
            return self._cached_cards

        self.ensure_cards_file()

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
