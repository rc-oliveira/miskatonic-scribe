"""Tests for ArkhamDBDeckExporter (.o8d OCTGN XML generation)."""

from __future__ import annotations

from miskatonic_scribe.domain.models import (
    CardMatchResult,
    ExtractedCardIntent,
    InvestigatorMatchResult,
)
from miskatonic_scribe.infrastructure.arkhamdb_deck_exporter import ArkhamDBDeckExporter


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
