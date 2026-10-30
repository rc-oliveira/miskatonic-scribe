"""Tests for ArkhamDBDeckExporter (.dek OCTGN XML generation and ArkhamDB publishing)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from miskatonic_scribe.domain.models import (
    CardMatchResult,
    ExtractedCardIntent,
    InvestigatorMatchResult,
)
from miskatonic_scribe.infrastructure.arkhamdb_deck_exporter import (
    ArkhamDBDeckExporter,
    ArkhamDBPublishError,
)


def test_generate_octgn_dek_includes_investigator_and_cards() -> None:
    investigator = InvestigatorMatchResult(
        raw_name="Diana Stanley",
        matched=True,
        card_code="05004",
        official_name="Diana Stanley",
        octgn_id="diana-guid-05004",
        similarity_score=100,
    )
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=2),
            matched=True,
            card_code="02066",
            official_name="Shrivelling",
            matched_xp=3,
            octgn_id="shrivelling-guid-02066",
            similarity_score=100,
        ),
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Machete", nivel=0, quantidade=1),
            matched=True,
            card_code="01020",
            official_name="Machete",
            matched_xp=0,
            octgn_id="machete-guid-01020",
            similarity_score=100,
        ),
    ]

    xml_str = ArkhamDBDeckExporter.generate_octgn_dek(
        deck_name="Miskatonic Scribe - Diana Stanley",
        matches=matches,
        investigator=investigator,
    )

    assert '<?xml version="1.0" encoding="utf-8"?>' in xml_str
    assert '<card qty="1" id="diana-guid-05004">Diana Stanley</card>' in xml_str
    assert '<card qty="2" id="shrivelling-guid-02066">Shrivelling</card>' in xml_str
    assert '<card qty="1" id="machete-guid-01020">Machete</card>' in xml_str


def test_publish_deck_via_web_session_returns_deck_url() -> None:
    session = MagicMock()

    login_get_resp = MagicMock()
    login_get_resp.text = '<input type="hidden" name="_csrf_token" value="csrf123" />'
    login_get_resp.raise_for_status = MagicMock()
    session.get.return_value = login_get_resp

    login_post_resp = MagicMock()
    login_post_resp.url = "https://arkhamdb.com/decks"
    login_post_resp.raise_for_status = MagicMock()

    save_post_resp = MagicMock()
    save_post_resp.url = "https://arkhamdb.com/deck/view/987654"
    save_post_resp.text = ""
    save_post_resp.raise_for_status = MagicMock()

    session.post.side_effect = [login_post_resp, save_post_resp]

    exporter = ArkhamDBDeckExporter(
        username="investigator_rodrigo",
        password="secret",
        session_factory=lambda: session,
    )
    assert exporter.has_credentials is True

    investigator = InvestigatorMatchResult(
        raw_name="Diana Stanley",
        matched=True,
        card_code="05004",
        official_name="Diana Stanley",
    )
    matches = [
        CardMatchResult(
            intent=ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=2),
            matched=True,
            card_code="01060",
            official_name="Shrivelling",
            matched_xp=0,
        )
    ]

    deck_url = exporter.publish_deck("Deck Diana", matches, investigator)
    assert deck_url == "https://arkhamdb.com/deck/view/987654"
    assert session.post.call_count == 2


def test_publish_deck_without_credentials_raises_error() -> None:
    exporter = ArkhamDBDeckExporter()
    assert exporter.has_credentials is False
    with pytest.raises(ArkhamDBPublishError, match="Credenciais do ArkhamDB"):
        exporter.publish_deck("Deck", [])
