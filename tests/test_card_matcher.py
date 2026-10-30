"""Tests for ArkhamDBCardRepository and ArkhamFuzzyCardMatcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from miskatonic_scribe.domain.models import ExtractedCardIntent
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher


class DummyHttpResponse:
    def __init__(self, json_data: Any) -> None:
        self._json_data = json_data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._json_data


def test_repository_downloads_cards_json_when_missing(
    tmp_path: Path,
    sample_arkhamdb_payload: List[dict[str, Any]],
) -> None:
    target_file = tmp_path / "cards.json"
    assert not target_file.exists()

    repo = ArkhamDBCardRepository(
        cards_path=target_file,
        http_get=lambda url, timeout: DummyHttpResponse(sample_arkhamdb_payload),
    )
    records = repo.load_cards()

    assert target_file.exists()
    assert len(records) == len(sample_arkhamdb_payload)


def test_matcher_finds_exact_xp_and_prioritizes_original_release(cards_json_file: Path) -> None:
    """Shrivelling lvl 0 should match 01060 (original Core) over 01560 (Revised Core reprint),
    and Shrivelling lvl 3 should match 02066."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    res_lvl0 = matcher.match_intent(
        ExtractedCardIntent(nome="Shrivelling", nivel=0, quantidade=2)
    )
    assert res_lvl0.matched is True
    assert res_lvl0.card_code == "01060"
    assert res_lvl0.matched_xp == 0
    assert res_lvl0.format_telegram_line() == "2x Shrivelling (Nível 0) - ID: 01060"

    res_lvl3 = matcher.match_intent(
        ExtractedCardIntent(nome="Shriveling", nivel=3, quantidade=2)  # Typo 'Shriveling' (one 'l')
    )
    assert res_lvl3.matched is True
    assert res_lvl3.card_code == "02066"
    assert res_lvl3.official_name == "Shrivelling"
    assert res_lvl3.matched_xp == 3
    assert res_lvl3.format_telegram_line() == "2x Shrivelling (Nível 3) - ID: 02066"


def test_matcher_surfaces_alternatives_for_distinct_subtitles(cards_json_file: Path) -> None:
    """Archaic Glyphs lvl 3 has two distinct upgraded cards (03192 Guiding Stones & 03193 Prophecy Foretold)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    res = matcher.match_intent(
        ExtractedCardIntent(nome="Archaic Glyphs", nivel=3, quantidade=1)
    )
    assert res.matched is True
    assert res.card_code == "03192"
    assert res.subname == "Guiding Stones"
    assert len(res.alternatives) == 1
    assert res.alternatives[0].code == "03193"
    assert res.alternatives[0].subname == "Prophecy Foretold"
    assert "Alternativas: 03193 (Prophecy Foretold)" in res.format_telegram_line()


def test_matcher_returns_unmatched_for_unknown_card(cards_json_file: Path) -> None:
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=70)

    res = matcher.match_intent(
        ExtractedCardIntent(nome="Black Lotus Ultra Dragon", nivel=0, quantidade=1)
    )
    assert res.matched is False
    assert res.card_code is None
    assert "Não encontrada no ArkhamDB" in res.format_telegram_line()
