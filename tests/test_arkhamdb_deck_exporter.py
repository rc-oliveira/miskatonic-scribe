"""Tests for ArkhamDBDeckExporter (.o8d OCTGN XML generation)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardMatchResult,
    ExtractedCardIntent,
    InvestigatorMatchResult,
)
from miskatonic_scribe.infrastructure.arkhamdb_deck_exporter import ArkhamDBDeckExporter

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def test_generate_octgn_dek_includes_investigator_and_cards() -> None:
    investigator = InvestigatorMatchResult(
        raw_name="Diana Stanley",
        matched=True,
        card_code="05004",
        official_name="Diana Stanley",
        octgn_id="guid1-05004:guid2-05004",
        similarity_score=100,
    )
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=2),
            matched=True,
            card_code="02066",
            official_name="Shrivelling",
            matched_xp=3,
            type_code="asset",
            octgn_id="shrivelling-guid-02066",
            similarity_score=100,
        ),
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Machete", nivel=0, quantidade=1),
            matched=True,
            card_code="01020",
            official_name="Machete",
            matched_xp=0,
            type_code="asset",
            octgn_id="machete-guid-01020",
            similarity_score=100,
        ),
    ]

    xml_str = ArkhamDBDeckExporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Diana Stanley",
        matches=matches,
        investigator=investigator,
    )

    assert '<?xml version="1.0" encoding="utf-8" standalone="yes"?>' in xml_str
    assert '<deck game="a6d114c7-2e2a-4896-ad8c-0330605c90bf" sleeveid="0">' in xml_str
    assert '<card qty="1" id="guid1-05004">Diana Stanley</card>' in xml_str
    assert '<card qty="1" id="guid2-05004">Diana Stanley</card>' in xml_str
    assert '<card qty="2" id="shrivelling-guid-02066">Shrivelling</card>' in xml_str
    assert '<card qty="1" id="machete-guid-01020">Machete</card>' in xml_str


def test_build_slots_map_aggregates_duplicate_card_entries() -> None:
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=1),
            matched=True,
            card_code="01060",
            official_name="Shrivelling",
            matched_xp=0,
        ),
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=1),
            matched=True,
            card_code="01060",
            official_name="Shrivelling",
            matched_xp=0,
        ),
    ]
    assert ArkhamDBDeckExporter.build_slots_map(matches) == {"01060": 2}


def test_generate_octgn_dek_resolves_guids_via_card_repository() -> None:
    """Investigator and cards with missing octgn_id should resolve GUIDs from alternate printings in repo."""
    mock_repo = MagicMock()
    mock_repo.load_cards.return_value = [
        # Official Joe Diamond printing in TCU with real OCTGN GUIDs
        ArkhamCardRecord(
            code="05002",
            real_name="Joe Diamond",
            name="Joe Diamond",
            type_code="investigator",
            octgn_id="b394f2bb-1494-4c61-866b-6f508dff1089:009a9055-355a-4d33-80d1-6877af906369",
        ),
        # Official Lesson Learned printing in Nathaniel Cho with real OCTGN GUID
        ArkhamCardRecord(
            code="60124",
            real_name="Lesson Learned",
            name="Lesson Learned",
            type_code="event",
            xp=0,
            octgn_id="bcd17fd2-970c-4329-a722-1c3d1223f66d",
        ),
    ]

    # Investigator matched to new pack (core_2026) where octgn_id is None
    investigator = InvestigatorMatchResult(
        raw_name="Joe Diamond",
        matched=True,
        card_code="12004",
        official_name="Joe Diamond",
        octgn_id=None,
    )
    # Card matched to new pack (core_2026) where octgn_id is None
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Lesson Learned", nivel=0, quantidade=2),
            matched=True,
            card_code="12022",
            official_name="Lesson Learned",
            matched_xp=0,
            type_code="event",
            octgn_id=None,
        ),
    ]

    exporter = ArkhamDBDeckExporter(card_repository=mock_repo)
    xml_str = exporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Joe Diamond",
        matches=matches,
        investigator=investigator,
    )

    # Investigator must resolve to the real 05002 GUIDs
    assert '<card qty="1" id="b394f2bb-1494-4c61-866b-6f508dff1089">Joe Diamond</card>' in xml_str
    assert '<card qty="1" id="009a9055-355a-4d33-80d1-6877af906369">Joe Diamond</card>' in xml_str
    # Lesson Learned must resolve to the real 60124 GUID
    assert '<card qty="2" id="bcd17fd2-970c-4329-a722-1c3d1223f66d">Lesson Learned</card>' in xml_str
    # Ensure no numeric IDs exist
    assert 'id="12004"' not in xml_str
    assert 'id="12022"' not in xml_str


def test_generate_octgn_dek_deterministic_uuidv5_fallback_when_no_octgn_id() -> None:
    """Cards and investigators with no OCTGN ID anywhere must receive a valid, deterministic UUIDv5."""
    investigator = InvestigatorMatchResult(
        raw_name="Custom Investigator",
        matched=True,
        card_code="99001",
        official_name="Custom Investigator",
        octgn_id=None,
    )
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Endurance", nivel=0, quantidade=2),
            matched=True,
            card_code="12017",
            official_name="Endurance",
            matched_xp=0,
            type_code="asset",
            octgn_id=None,
        ),
    ]

    xml_str = ArkhamDBDeckExporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Custom",
        matches=matches,
        investigator=investigator,
    )

    ids = re.findall(r'<card\s+[^>]*id="([^"]+)"', xml_str)
    # 2 for investigator (card + minicard) + 1 for Endurance
    assert len(ids) == 3
    for card_id in ids:
        assert GUID_RE.match(card_id), f"ID '{card_id}' is not a valid 36-char GUID!"

    # Ensure deterministic output
    xml_str2 = ArkhamDBDeckExporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Custom",
        matches=matches,
        investigator=investigator,
    )
    assert xml_str == xml_str2


def test_generate_octgn_dek_ensures_zero_numeric_ids_in_output() -> None:
    """Ensure that 100% of card elements in generated XML have valid non-numeric GUIDs."""
    investigator = InvestigatorMatchResult(
        raw_name="Joe Diamond",
        matched=True,
        card_code="12004",
        official_name="Joe Diamond",
        octgn_id="12004",  # Simulating a numeric ID accidentally passed
    )
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Endurance", nivel=0, quantidade=2),
            matched=True,
            card_code="12017",
            official_name="Endurance",
            octgn_id="12017",  # Numeric ID
            type_code="asset",
        ),
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Machete", nivel=0, quantidade=2),
            matched=True,
            card_code="01020",
            official_name="Machete",
            octgn_id="d959a671-030a-40ff-909d-235b815690b1",
            type_code="asset",
        ),
    ]

    xml_str = ArkhamDBDeckExporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Mixed IDs Test",
        matches=matches,
        investigator=investigator,
    )

    # No 5-digit numeric IDs must appear in <card id="..."> attributes
    numeric_card_ids = re.findall(r'<card\s+[^>]*id="(\d+)"', xml_str)
    assert numeric_card_ids == [], f"Found numeric card IDs in XML: {numeric_card_ids}"

    # Verify Machete's official GUID is preserved
    assert 'id="d959a671-030a-40ff-909d-235b815690b1"' in xml_str
