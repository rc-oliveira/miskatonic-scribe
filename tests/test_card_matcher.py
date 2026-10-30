"""Tests for ArkhamDBCardRepository and ArkhamFuzzyCardMatcher."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List

import requests

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


def test_repository_auto_refreshes_expired_cards_and_falls_back_if_offline(
    cards_json_file: Path,
    sample_arkhamdb_payload: List[dict[str, Any]],
) -> None:
    """When cards.json exceeds ARKHAMDB_REFRESH_DAYS, repository refreshes it automatically;
    if ArkhamDB is temporarily offline, it falls back gracefully to the existing local file."""
    now = 1_800_000_000.0
    expired_mtime = now - (35 * 86400)  # 35 days old (> 30 days TTL)
    os.utime(cards_json_file, (expired_mtime, expired_mtime))

    # 1. Test graceful fallback when ArkhamDB is offline during periodic refresh
    def failing_get(url: str, timeout: float) -> Any:
        raise requests.ConnectionError("ArkhamDB temporarily unreachable")

    repo_offline = ArkhamDBCardRepository(
        cards_path=cards_json_file,
        refresh_days=30,
        http_get=failing_get,
        clock=lambda: now,
    )
    fallback_records = repo_offline.load_cards()
    assert len(fallback_records) == len(sample_arkhamdb_payload)

    # 2. Test successful periodic refresh adding a new card
    updated_payload = [
        *sample_arkhamdb_payload,
        {
            "code": "09001",
            "name": "Runic Axe",
            "real_name": "Runic Axe",
            "xp": 0,
            "pack_code": "tsk",
            "faction_code": "guardian",
            "type_code": "asset",
        },
    ]
    repo_online = ArkhamDBCardRepository(
        cards_path=cards_json_file,
        refresh_days=30,
        http_get=lambda url, timeout: DummyHttpResponse(updated_payload),
        clock=lambda: now,
    )
    refreshed_records = repo_online.load_cards()
    assert len(refreshed_records) == len(sample_arkhamdb_payload) + 1


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


def test_matcher_upgrades_xp0_weak_match_to_exact_name_higher_xp(cards_json_file: Path) -> None:
    """When user omits XP (nivel=0) for 'Counterspell' (which only exists at XP 2, ID 04110),
    matcher must choose Counterspell (100%, XP 2) over Counterpunch (67%, XP 0)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    res = matcher.match_intent(ExtractedCardIntent(nome="Counterspell", nivel=0, quantidade=1))
    assert res.matched is True
    assert res.card_code == "04110"
    assert res.official_name == "Counterspell"
    assert res.matched_xp == 2


def test_matcher_matches_investigator_diana_stanley(cards_json_file: Path) -> None:
    """Investigator matcher must identify Diana Stanley (ID 05004)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    inv = matcher.match_investigator("Diana Stanley")
    assert inv.matched is True
    assert inv.card_code == "05004"
    assert inv.official_name == "Diana Stanley"
    assert "05004" in inv.format_telegram_line()

