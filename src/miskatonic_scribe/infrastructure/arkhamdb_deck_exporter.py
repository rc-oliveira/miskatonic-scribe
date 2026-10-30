"""ArkhamDB Deck Exporter (.dek OCTGN XML) and Direct Account Deck Publisher."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Callable, Dict, Optional, Sequence

import requests

from miskatonic_scribe.domain.exceptions import MiskatonicScribeError
from miskatonic_scribe.domain.models import CardMatchResult, InvestigatorMatchResult

logger = logging.getLogger(__name__)

# Official Arkham Horror LCG OCTGN Game GUID used by ArkhamDB's Import Deck feature
ARKHAM_OCTGN_GAME_GUID = "a6d114c7-2e2a-4896-ad8c-0330605c90bf"
DEFAULT_INVESTIGATOR_CODE = "01001"  # Roland Banks fallback if none provided


class ArkhamDBPublishError(MiskatonicScribeError):
    """Raised when publishing a deck directly to an ArkhamDB account fails."""


class ArkhamDBDeckExporter:
    """Generates ArkhamDB-compatible .dek (OCTGN) files and publishes decks to ArkhamDB."""

    def __init__(
        self,
        base_url: str = "https://arkhamdb.com",
        username: Optional[str] = None,
        password: Optional[str] = None,
        access_token: Optional[str] = None,
        session_factory: Callable[[], requests.Session] = requests.Session,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = (username or "").strip()
        self._password = (password or "").strip()
        self._access_token = (access_token or "").strip()
        self._session_factory = session_factory
        self._timeout = timeout

    @property
    def has_credentials(self) -> bool:
        """Return True if either OAuth2 access_token or username+password are configured."""
        return bool(self._access_token or (self._username and self._password))

    @staticmethod
    def build_slots_map(matches: Sequence[CardMatchResult]) -> Dict[str, int]:
        """Aggregate matched cards into ArkhamDB slots format: {'01088': 2, '05032': 2}."""
        slots: Dict[str, int] = {}
        for match in matches:
            if match.matched and match.card_code:
                code = str(match.card_code)
                slots[code] = slots.get(code, 0) + match.intent.quantidade
        return slots

    @staticmethod
    def generate_octgn_dek(
        deck_name: str,
        matches: Sequence[CardMatchResult],
        investigator: Optional[InvestigatorMatchResult] = None,
    ) -> str:
        """Generate an OCTGN .dek XML string ready for 'Import Deck' on ArkhamDB."""
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<deck game="{ARKHAM_OCTGN_GAME_GUID}">',
            '  <section name="Investigator" shared="False">',
        ]

        if investigator and investigator.matched and investigator.card_code:
            inv_id = html.escape(investigator.octgn_id or investigator.card_code, quote=True)
            inv_name = html.escape(investigator.official_name or investigator.raw_name, quote=False)
            lines.append(f'    <card qty="1" id="{inv_id}">{inv_name}</card>')

        lines.append("  </section>")
        lines.append('  <section name="Card" shared="False">')

        # Aggregate quantities by card_code while preserving first-seen metadata
        aggregated: Dict[str, tuple[int, CardMatchResult]] = {}
        for match in matches:
            if match.matched and match.card_code:
                code = match.card_code
                prev_qty, _ = aggregated.get(code, (0, match))
                aggregated[code] = (prev_qty + match.intent.quantidade, match)

        for code, (qty, match) in aggregated.items():
            card_id = html.escape(match.octgn_id or code, quote=True)
            card_name = html.escape(match.official_name or match.intent.nome, quote=False)
            lines.append(f'    <card qty="{qty}" id="{card_id}">{card_name}</card>')

        lines.append("  </section>")
        lines.append(f"  <notes><![CDATA[{deck_name}]]></notes>")
        lines.append("</deck>")
        return "\n".join(lines) + "\n"

    def _publish_via_oauth2(
        self,
        session: requests.Session,
        deck_name: str,
        investigator_code: str,
        slots: Dict[str, int],
    ) -> str:
        """Create and save deck using ArkhamDB OAuth2 API."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        new_resp = session.post(
            f"{self._base_url}/api/oauth2/deck/new",
            headers=headers,
            data={"investigator": investigator_code, "name": deck_name},
            timeout=self._timeout,
        )
        new_resp.raise_for_status()
        new_data: Dict[str, Any] = new_resp.json()
        deck_id = new_data.get("id")
        if not deck_id:
            raise ArkhamDBPublishError(f"ArkhamDB OAuth2 did not return deck id: {new_data}")

        save_resp = session.put(
            f"{self._base_url}/api/oauth2/deck/save/{deck_id}",
            headers=headers,
            data={
                "name": deck_name,
                "investigator": investigator_code,
                "slots": json.dumps(slots),
            },
            timeout=self._timeout,
        )
        save_resp.raise_for_status()
        return f"{self._base_url}/deck/view/{deck_id}"

    def _publish_via_web_session(
        self,
        session: requests.Session,
        deck_name: str,
        investigator_code: str,
        slots: Dict[str, int],
    ) -> str:
        """Log in to ArkhamDB via web session and save a new deck."""
        login_url = f"{self._base_url}/login"
        login_page = session.get(login_url, timeout=self._timeout)
        login_page.raise_for_status()

        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            login_page.text,
        )
        csrf_token = csrf_match.group(1) if csrf_match else ""

        login_check_resp = session.post(
            f"{self._base_url}/login_check",
            data={
                "_username": self._username,
                "_password": self._password,
                "_csrf_token": csrf_token,
            },
            timeout=self._timeout,
            allow_redirects=True,
        )
        login_check_resp.raise_for_status()
        if "/login" in login_check_resp.url:
            raise ArkhamDBPublishError(
                "Falha de autenticação no ArkhamDB. Verifique ARKHAMDB_USERNAME e ARKHAMDB_PASSWORD."
            )

        save_resp = session.post(
            f"{self._base_url}/deck/save",
            data={
                "name": deck_name,
                "investigator": investigator_code,
                "content": json.dumps(slots),
            },
            timeout=self._timeout,
            allow_redirects=True,
        )
        save_resp.raise_for_status()

        deck_id_match = re.search(r"/deck/view/(\d+)", save_resp.url) or re.search(
            r"/deck/(?:view|edit)/(\d+)", save_resp.text
        )
        if deck_id_match:
            return f"{self._base_url}/deck/view/{deck_id_match.group(1)}"
        return f"{self._base_url}/decks"

    def publish_deck(
        self,
        deck_name: str,
        matches: Sequence[CardMatchResult],
        investigator: Optional[InvestigatorMatchResult] = None,
    ) -> str:
        """Publish confirmed deck directly to ArkhamDB and return the deck URL."""
        if not self.has_credentials:
            raise ArkhamDBPublishError(
                "Credenciais do ArkhamDB (ARKHAMDB_USERNAME/ARKHAMDB_PASSWORD ou ARKHAMDB_ACCESS_TOKEN) "
                "não estão configuradas no .env."
            )

        slots = self.build_slots_map(matches)
        if not slots:
            raise ArkhamDBPublishError("Nenhuma carta válida encontrada para publicar no ArkhamDB.")

        investigator_code = (
            investigator.card_code
            if (investigator and investigator.matched and investigator.card_code)
            else DEFAULT_INVESTIGATOR_CODE
        )

        session = self._session_factory()
        try:
            if self._access_token:
                url = self._publish_via_oauth2(session, deck_name, investigator_code, slots)
            else:
                url = self._publish_via_web_session(session, deck_name, investigator_code, slots)
        except ArkhamDBPublishError:
            raise
        except requests.RequestException as exc:
            logger.exception("Network error while publishing deck to ArkhamDB: %s", exc)
            raise ArkhamDBPublishError(f"Erro de conexão ao publicar deck no ArkhamDB: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected error publishing deck to ArkhamDB: %s", exc)
            raise ArkhamDBPublishError(f"Erro ao publicar deck no ArkhamDB: {exc}") from exc
        else:
            logger.info("Published deck '%s' to ArkhamDB: %s", deck_name, url)
            return url
