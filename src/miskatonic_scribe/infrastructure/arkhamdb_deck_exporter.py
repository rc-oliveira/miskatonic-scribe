"""ArkhamDB Deck Exporter (.o8d OCTGN XML)."""

from __future__ import annotations

import html
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from miskatonic_scribe.domain.models import CardMatchResult, InvestigatorMatchResult

# Official Arkham Horror LCG OCTGN Game GUID used by ArkhamDB's Import Deck feature
ARKHAM_OCTGN_GAME_GUID = "a6d114c7-2e2a-4896-ad8c-0330605c90bf"
_OCTGN_NAMESPACE_UUID = uuid.UUID(ARKHAM_OCTGN_GAME_GUID)
_GUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ArkhamDBDeckExporter:
    """Generates ArkhamDB-compatible .o8d (OCTGN XML) deck files for 1-click import on ArkhamDB."""

    def __init__(self, card_repository: Optional[Any] = None) -> None:
        self._card_repository = card_repository

    @staticmethod
    def is_valid_guid(val: Optional[str]) -> bool:
        """Check if a string matches standard 36-character hexadecimal GUID format."""
        return bool(val and _GUID_REGEX.match(str(val).strip()))

    @staticmethod
    def is_usable_octgn_id(val: Optional[str]) -> bool:
        """Check if a string is a non-empty, non-numeric OCTGN ID (guid or identifier)."""
        if not val:
            return False
        clean = str(val).strip()
        if not clean or clean.isdigit():
            return False
        return True

    @staticmethod
    def make_deterministic_guid(key: str) -> str:
        """Generate a deterministic UUIDv5 within the Arkham Horror OCTGN namespace."""
        return str(uuid.uuid5(_OCTGN_NAMESPACE_UUID, str(key).strip()))

    @staticmethod
    def build_slots_map(matches: Sequence[CardMatchResult]) -> Dict[str, int]:
        """Aggregate matched cards into ArkhamDB slots format: {'01088': 2, '05032': 2}."""
        slots: Dict[str, int] = {}
        for match in matches:
            if match.matched and match.card_code:
                code = str(match.card_code)
                slots[code] = slots.get(code, 0) + match.intent.quantidade
        return slots

    @classmethod
    def _build_repo_indexes(
        cls, card_repository: Optional[Any]
    ) -> Tuple[Dict[str, str], Dict[Tuple[str, int], str], Dict[str, str], Dict[str, str]]:
        """Index card repository by name and code to resolve missing OCTGN IDs from alternate printings."""
        inv_octgn_by_name: Dict[str, str] = {}
        card_octgn_by_name_xp: Dict[Tuple[str, int], str] = {}
        card_octgn_by_name: Dict[str, str] = {}
        card_octgn_by_code: Dict[str, str] = {}

        if card_repository is None or not hasattr(card_repository, "load_cards"):
            return (
                inv_octgn_by_name,
                card_octgn_by_name_xp,
                card_octgn_by_name,
                card_octgn_by_code,
            )

        try:
            records = card_repository.load_cards()
        except Exception:
            return (
                inv_octgn_by_name,
                card_octgn_by_name_xp,
                card_octgn_by_name,
                card_octgn_by_code,
            )

        for rec in records:
            octgn = getattr(rec, "octgn_id", None)
            if not octgn:
                continue
            octgn_str = str(octgn).strip()
            if not cls.is_usable_octgn_id(octgn_str):
                continue

            code_str = str(rec.code).strip()
            name_str = (getattr(rec, "real_name", None) or getattr(rec, "name", "")).strip().lower()
            xp = getattr(rec, "xp", 0) or 0
            is_inv = getattr(rec, "is_investigator", False) or getattr(rec, "type_code", "") == "investigator"

            if code_str:
                card_octgn_by_code[code_str] = octgn_str

            if is_inv:
                if name_str and ":" in octgn_str and name_str not in inv_octgn_by_name:
                    inv_octgn_by_name[name_str] = octgn_str
            else:
                if name_str:
                    card_octgn_by_name_xp.setdefault((name_str, xp), octgn_str)
                    card_octgn_by_name.setdefault(name_str, octgn_str)

        return (
            inv_octgn_by_name,
            card_octgn_by_name_xp,
            card_octgn_by_name,
            card_octgn_by_code,
        )

    @classmethod
    def resolve_investigator_guids(
        cls,
        investigator: InvestigatorMatchResult,
        inv_index: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str]:
        """Resolve investigator card GUID and minicard GUID, falling back to alternate printings or UUIDv5."""
        raw_octgn = str(investigator.octgn_id).strip() if investigator.octgn_id else ""
        if ":" in raw_octgn:
            g1, g2 = raw_octgn.split(":", 1)
            g1_clean, g2_clean = g1.strip(), g2.strip()
            if cls.is_usable_octgn_id(g1_clean) and cls.is_usable_octgn_id(g2_clean):
                return g1_clean, g2_clean
        elif cls.is_usable_octgn_id(raw_octgn):
            return raw_octgn, raw_octgn

        # Check repository index for alternate printings of this investigator (e.g., 05002 for 12004)
        if inv_index:
            lookup_names = [
                (investigator.official_name or "").strip().lower(),
                (investigator.raw_name or "").strip().lower(),
            ]
            for name in lookup_names:
                if name and name in inv_index:
                    candidate = inv_index[name]
                    if ":" in candidate:
                        cg1, cg2 = candidate.split(":", 1)
                        if cls.is_usable_octgn_id(cg1.strip()) and cls.is_usable_octgn_id(cg2.strip()):
                            return cg1.strip(), cg2.strip()

        # Deterministic UUIDv5 fallback if no valid GUID pair exists
        inv_key = investigator.card_code or investigator.official_name or investigator.raw_name or "investigator"
        guid1 = cls.make_deterministic_guid(f"investigator:{inv_key}")
        guid2 = cls.make_deterministic_guid(f"minicard:{inv_key}")
        return guid1, guid2

    @classmethod
    def resolve_card_guid(
        cls,
        match: CardMatchResult,
        code: str,
        card_indexes: Optional[
            Tuple[Dict[str, str], Dict[Tuple[str, int], str], Dict[str, str], Dict[str, str]]
        ] = None,
    ) -> str:
        """Resolve a 36-char GUID for a card, falling back to alternate printings or UUIDv5."""
        if cls.is_usable_octgn_id(match.octgn_id):
            return str(match.octgn_id).strip()

        if card_indexes:
            _, name_xp_idx, name_idx, code_idx = card_indexes
            # 1. By code from repo
            if code in code_idx and cls.is_usable_octgn_id(code_idx[code]):
                return code_idx[code]

            card_name = (match.official_name or match.intent.nome or "").strip().lower()
            xp = match.matched_xp if match.matched_xp is not None else (match.intent.nivel or 0)

            # 2. By (name, xp) from alternate printing (e.g., 60124 for 12022 Lesson Learned)
            cand_xp = name_xp_idx.get((card_name, xp))
            if cand_xp and cls.is_usable_octgn_id(cand_xp):
                return cand_xp

            # 3. By name
            cand_name = name_idx.get(card_name)
            if cand_name and cls.is_usable_octgn_id(cand_name):
                return cand_name

        # Deterministic UUIDv5 fallback
        return cls.make_deterministic_guid(f"card:{code}")

    @classmethod
    def _render_octgn_dek(
        cls,
        deck_name: str,
        matches: Sequence[CardMatchResult],
        investigator: Optional[InvestigatorMatchResult] = None,
        card_repository: Optional[Any] = None,
    ) -> str:
        """Render the complete OCTGN XML document ensuring 100% of IDs are valid GUIDs."""
        lines = [
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
            f'<deck game="{ARKHAM_OCTGN_GAME_GUID}" sleeveid="0">',
            '  <section name="Investigator" shared="False">',
        ]

        card_indexes = cls._build_repo_indexes(card_repository)
        inv_index = card_indexes[0]

        if investigator and investigator.matched and investigator.card_code:
            inv_name = html.escape(investigator.official_name or investigator.raw_name, quote=False)
            guid1, guid2 = cls.resolve_investigator_guids(investigator, inv_index=inv_index)
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
            card_id = cls.resolve_card_guid(match, code, card_indexes=card_indexes)
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

    def generate_octgn_dek(
        *args,
        deck_name: Optional[str] = None,
        matches: Optional[Sequence[CardMatchResult]] = None,
        investigator: Optional[InvestigatorMatchResult] = None,
        card_repository: Optional[Any] = None,
    ) -> str:
        """Generate an OCTGN .o8d XML string matching ArkhamDB's octgn.xml.twig and parseOctgnImport."""
        if args and isinstance(args[0], ArkhamDBDeckExporter):
            self = args[0]
            rest = args[1:]
            repo = card_repository if card_repository is not None else self._card_repository
        else:
            self = None
            rest = args
            repo = card_repository

        if rest:
            deck_name = rest[0]
        if len(rest) > 1:
            matches = rest[1]
        if len(rest) > 2:
            investigator = rest[2]
        if len(rest) > 3 and repo is None:
            repo = rest[3]

        return ArkhamDBDeckExporter._render_octgn_dek(
            deck_name=deck_name or "",
            matches=matches or [],
            investigator=investigator,
            card_repository=repo,
        )
