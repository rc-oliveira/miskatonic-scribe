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


def test_matcher_never_swaps_ive_had_worse_for_illegal_youve_had_worse(cards_json_file: Path) -> None:
    """'I've had worse...' (XP 0 default) must match 'I've had worse…' (ID 02261, Guardian XP 2)
    instead of '"You've had worse..."' (ID 11054, Seeker XP 0), and for Diana Stanley (Guardian 0-2)
    must also exclude the illegal Guardian XP 4 variant (02107)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    diana = matcher.match_investigator("Diana Stanley")
    assert diana.matched is True
    assert diana.card_code == "05004"

    # 1. With Diana Stanley as investigator: matches legal Guardian XP 2 (02261) and excludes XP 4 (02107) and Seeker (11054)
    res_diana = matcher.match_intent(
        ExtractedCardIntent(nome="I've had worse...", nivel=0, quantidade=2),
        investigator=diana,
    )
    assert res_diana.matched is True
    assert res_diana.card_code == "02261"
    assert res_diana.official_name == "I've had worse…"
    assert res_diana.matched_xp == 2
    assert all(alt.code != "02107" for alt in res_diana.alternatives)

    # 2. Even if LLM extracted '"You've had worse..."', Diana Stanley cannot take Seeker cards,
    # so matcher resolves to the legal Guardian XP 2 card 'I've had worse…' (02261)
    res_disambiguated = matcher.match_intent(
        ExtractedCardIntent(nome='"You\'ve had worse..."', nivel=0, quantidade=2),
        investigator=diana,
    )
    assert res_disambiguated.matched is True
    assert res_disambiguated.card_code == "02261"
    assert res_disambiguated.official_name == "I've had worse…"
    assert res_disambiguated.matched_xp == 2

    # 3. Even without investigator context, exact name 'I've had worse...' (100%) beats '"You've had worse..."' (87%)
    res_no_inv = matcher.match_intent(
        ExtractedCardIntent(nome="I've had worse...", nivel=0, quantidade=2),
    )
    assert res_no_inv.matched is True
    assert res_no_inv.card_code == "02261"
    assert res_no_inv.official_name == "I've had worse…"

    # 4. Explicitly off-class card with no close legal match ('Dr. Milan Christopher', Seeker) is rejected for Diana
    res_illegal = matcher.match_intent(
        ExtractedCardIntent(nome="Dr. Milan Christopher", nivel=0, quantidade=1),
        investigator=diana,
    )
    assert res_illegal.matched is False


def test_matcher_matches_investigator_diana_stanley(cards_json_file: Path) -> None:
    """Investigator matcher must identify Diana Stanley (ID 05004)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    inv = matcher.match_investigator("Diana Stanley")
    assert inv.matched is True
    assert inv.card_code == "05004"
    assert inv.official_name == "Diana Stanley"
    assert "05004" in inv.format_telegram_line()



def test_matcher_tiebreak_prefers_core_2026_when_deck_contains_signatures(cards_json_file: Path) -> None:
    """When two investigators have the exact same name (Joe Diamond 05002 vs 12004),
    the matcher must pick 12004 if deck contains its signature cards (Dead Ends / Detective's Intuition)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    inv = matcher.match_investigator(
        "Joe Diamond",
        candidate_card_names=["Dead Ends", "Detective's Intuition", "Machete"],
    )
    assert inv.matched is True
    assert inv.card_code == "12004"
    assert inv.official_name == "Joe Diamond"


def test_matcher_tiebreak_prefers_tcu_when_deck_contains_tcu_signature(cards_json_file: Path) -> None:
    """When deck contains 05002 signature (Detective's Colt 1911s), matcher must pick 05002."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    inv = matcher.match_investigator(
        "Joe Diamond",
        candidate_card_names=["Detective's Colt 1911s", "Machete"],
    )
    assert inv.matched is True
    assert inv.card_code == "05002"
    assert inv.official_name == "Joe Diamond"


def test_matcher_tiebreak_prefers_core_when_qualifier_in_name(cards_json_file: Path) -> None:
    """When investigator name explicitly mentions 'Core', pack bonus must favor 12004 (pack core_2026)."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    inv = matcher.match_investigator("Joe Diamond (Core 7.2)")
    assert inv.matched is True
    assert inv.card_code == "12004"



def test_matcher_matches_bilingual_portuguese_and_english_cards(cards_json_file: Path) -> None:
    """Matcher should seamlessly match cards searched by English name, Portuguese name, or accent variations."""
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)

    # 1. Portuguese match: "Facão" -> 01020 (Machete)
    res_pt_facao = matcher.match_intent(ExtractedCardIntent(nome="Facão", nivel=0, quantidade=2))
    assert res_pt_facao.matched is True
    assert res_pt_facao.card_code == "01020"

    # 2. English match: "Machete" -> 01020
    res_en_machete = matcher.match_intent(ExtractedCardIntent(nome="Machete", nivel=0, quantidade=2))
    assert res_en_machete.matched is True
    assert res_en_machete.card_code == "01020"

    # 3. Portuguese match with accents: "Estoque de Emergência" -> 01088 (Emergency Cache)
    res_pt_cache = matcher.match_intent(ExtractedCardIntent(nome="Estoque de Emergência", nivel=0, quantidade=2))
    assert res_pt_cache.matched is True
    assert res_pt_cache.card_code == "01088"

    # 4. Portuguese match without accents and lower: "estoque de emergencia" -> 01088
    res_pt_cache_norm = matcher.match_intent(ExtractedCardIntent(nome="estoque de emergencia", nivel=0, quantidade=2))
    assert res_pt_cache_norm.matched is True
    assert res_pt_cache_norm.card_code == "01088"

    # 5. Portuguese match: "Peitar" -> 01089 (Guts)
    res_pt_guts = matcher.match_intent(ExtractedCardIntent(nome="Peitar", nivel=0, quantidade=1))
    assert res_pt_guts.matched is True
    assert res_pt_guts.card_code == "01089"


def test_repository_downloads_and_merges_en_and_pt_cards(tmp_path: Path) -> None:
    """ArkhamDBCardRepository downloads EN and PT catalogs and merges pt_name/pt_subname by code."""
    target_file = tmp_path / "cards.json"
    en_cards = [
        {"code": "01020", "name": "Machete", "real_name": "Machete", "xp": 0, "type_code": "asset", "pack_code": "core"},
        {"code": "01088", "name": "Emergency Cache", "real_name": "Emergency Cache", "xp": 0, "type_code": "event", "pack_code": "core"},
    ]
    pt_cards = [
        {"code": "01020", "name": "Facão", "subname": None},
        {"code": "01088", "name": "Estoque de Emergência", "subname": None},
    ]

    def mock_get(url: str, timeout: float) -> Any:
        if "pt.arkhamdb.com" in url:
            return DummyHttpResponse(pt_cards)
        return DummyHttpResponse(en_cards)

    repo = ArkhamDBCardRepository(cards_path=target_file, http_get=mock_get)
    records = repo.load_cards()

    assert len(records) == 2
    rec_by_code = {r.code: r for r in records}
    assert rec_by_code["01020"].real_name == "Machete"
    assert rec_by_code["01020"].pt_name == "Facão"
    assert rec_by_code["01088"].real_name == "Emergency Cache"
    assert rec_by_code["01088"].pt_name == "Estoque de Emergência"
