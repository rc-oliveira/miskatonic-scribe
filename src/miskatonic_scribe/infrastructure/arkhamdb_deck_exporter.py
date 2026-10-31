"""ArkhamDB Deck Exporter (.o8d OCTGN XML)."""

from __future__ import annotations

import html
from typing import Dict, List, Optional, Sequence

from miskatonic_scribe.domain.models import CardMatchResult, InvestigatorMatchResult

# Official Arkham Horror LCG OCTGN Game GUID used by ArkhamDB's Import Deck feature
ARKHAM_OCTGN_GAME_GUID = "a6d114c7-2e2a-4896-ad8c-0330605c90bf"


class ArkhamDBDeckExporter:
    """Generates ArkhamDB-compatible .o8d (OCTGN XML) deck files for 1-click import on ArkhamDB."""

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
        """Generate an OCTGN .o8d XML string matching ArkhamDB's octgn.xml.twig and parseOctgnImport."""
        lines = [
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
            f'<deck game="{ARKHAM_OCTGN_GAME_GUID}" sleeveid="0">',
            '  <section name="Investigator" shared="False">',
        ]

        if investigator and investigator.matched and investigator.card_code:
            inv_name = html.escape(investigator.official_name or investigator.raw_name, quote=False)
            raw_octgn = investigator.octgn_id or investigator.card_code
            if ":" in raw_octgn:
                guid1, guid2 = raw_octgn.split(":", 1)
            else:
                guid1, guid2 = raw_octgn, raw_octgn
            lines.append(f'    <card qty="1" id="{html.escape(guid1, quote=True)}">{inv_name}</card>')
            lines.append(f'    <card qty="1" id="{html.escape(guid2, quote=True)}">{inv_name}</card>')

        lines.append("  </section>")

        # Aggregate quantities by card_code while preserving first-seen metadata
        aggregated: Dict[str, tuple[int, CardMatchResult]] = {}
        for match in matches:
            if match.matched and match.card_code:
                code = match.card_code
                prev_qty, _ = aggregated.get(code, (0, match))
                aggregated[code] = (prev_qty + match.intent.quantidade, match)

        sections: Dict[str, List[str]] = {
            "Asset": [],
            "Event": [],
            "Skill": [],
            "Weakness": [],
        }

        for code, (qty, match) in aggregated.items():
            card_id = html.escape(match.octgn_id or code, quote=True)
            card_name = html.escape(match.official_name or match.intent.nome, quote=False)
            card_line = f'    <card qty="{qty}" id="{card_id}">{card_name}</card>'
            tcode = (match.type_code or "asset").lower()
            if tcode == "event":
                sections["Event"].append(card_line)
            elif tcode == "skill":
                sections["Skill"].append(card_line)
            elif tcode in {"treachery", "enemy", "weakness"}:
                sections["Weakness"].append(card_line)
            else:
                sections["Asset"].append(card_line)

        for section_name in ("Asset", "Event", "Skill", "Weakness"):
            lines.append(f'  <section name="{section_name}" shared="False">')
            lines.extend(sections[section_name])
            lines.append("  </section>")

        lines.append(f"<notes><![CDATA[{deck_name}]]></notes>")
        lines.append("</deck>")
        return "\n".join(lines) + "\n"
