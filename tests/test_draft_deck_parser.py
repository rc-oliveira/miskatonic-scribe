"""Unit and integration tests for deterministic DraftDeck command parser (Issue #1 Stateful CRUD)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miskatonic_scribe.domain.models import DraftDeck, ExtractedCardIntent
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.services.card_matcher import ArkhamFuzzyCardMatcher
from miskatonic_scribe.services.draft_deck_parser import DraftDeckCommandParser


@pytest.fixture
def parser_and_repo(cards_json_file: Path) -> tuple[DraftDeckCommandParser, ArkhamDBCardRepository]:
    repo = ArkhamDBCardRepository(cards_path=cards_json_file)
    matcher = ArkhamFuzzyCardMatcher(repository=repo, similarity_threshold=65)
    parser = DraftDeckCommandParser(repository=repo, matcher=matcher)
    return parser, repo


def test_swap_replaces_unmatched_item_with_valid_card_by_id(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01060": 2},
        unmatched=["Bounty Contracts", "CartaEstranha"],
    )

    # Troca o termo de unmatched "Bounty Contracts" diretamente pelo card 01020 (Machete)
    report = parser.parse_and_apply(draft, "swap Bounty Contracts por 01020")
    assert report.applied_count == 1
    assert "Bounty Contracts" not in draft.unmatched
    assert "CartaEstranha" in draft.unmatched
    assert draft.cards.get("01020") == 1
    assert draft.cards.get("01060") == 2


def test_swap_cards_with_equal_quantity(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"03034": 2},  # 2x Astral Travel
        unmatched=[],
    )

    # Troca Astral Travel por Machete
    report = parser.parse_and_apply(draft, "trocar Astral Travel por Machete")
    assert report.applied_count == 1
    assert "03034" not in draft.cards
    assert draft.cards.get("01020") == 2  # Mantém as 2 cópias da origem


def test_swap_two_numeric_ids_without_delimiter(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"03034": 2},
        unmatched=[],
    )

    # swap 03034 01020
    report = parser.parse_and_apply(draft, "swap 03034 01020")
    assert report.applied_count == 1
    assert "03034" not in draft.cards
    assert draft.cards.get("01020") == 2


def test_add_card_by_numeric_id(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01020": 1},
        unmatched=["Machete"],
    )

    # +1 01020 deve somar 1 cópia e remover Machete de unmatched
    report = parser.parse_and_apply(draft, "+1 01020")
    assert report.applied_count == 1
    assert draft.cards["01020"] == 2
    assert "Machete" not in draft.unmatched


def test_add_card_by_name_and_level(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={},
        unmatched=[],
    )

    report = parser.parse_and_apply(draft, "+2 Shrivelling 3")
    assert report.applied_count == 1
    assert draft.cards.get("02066") == 2  # Shrivelling nível 3


def test_add_invalid_code_provides_feedback_without_modifying_cards(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(investigator_code="05004", cards={"01020": 1}, unmatched=[])

    report = parser.parse_and_apply(draft, "+1 99999")
    assert report.applied_count == 0
    assert any("99999" in fb for fb in report.feedbacks)
    assert draft.cards == {"01020": 1}


def test_remove_card_by_id_and_by_name(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01020": 2, "02066": 2},
        unmatched=["CartaInexistente"],
    )

    # Remoção parcial por nome
    report1 = parser.parse_and_apply(draft, "-1 Machete")
    assert report1.applied_count == 1
    assert draft.cards["01020"] == 1

    # Remoção total por código
    report2 = parser.parse_and_apply(draft, "- 02066")
    assert report2.applied_count == 1
    assert "02066" not in draft.cards

    # Remoção de item em unmatched
    report3 = parser.parse_and_apply(draft, "remove CartaInexistente")
    assert report3.applied_count == 1
    assert "CartaInexistente" not in draft.unmatched


def test_change_investigator_by_id_and_name(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(investigator_code=None, cards={}, unmatched=["Investigador: Desconhecido"])

    # Por ID válido
    report1 = parser.parse_and_apply(draft, "inv 05004")
    assert report1.applied_count == 1
    assert draft.investigator_code == "05004"
    assert not any("Investigador:" in u for u in draft.unmatched)

    # Por nome válido
    report2 = parser.parse_and_apply(draft, "investigador Diana Stanley")
    assert report2.applied_count == 1
    assert draft.investigator_code == "05004"


def test_change_investigator_with_card_that_is_not_investigator(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(investigator_code="05004", cards={}, unmatched=[])

    # 01020 é Machete (não é investigador)
    report = parser.parse_and_apply(draft, "inv 01020")
    assert report.applied_count == 0
    assert any("não é um investigador" in fb for fb in report.feedbacks)
    assert draft.investigator_code == "05004"


def test_multiple_commands_in_single_text_block(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code=None,
        cards={"01020": 1},
        unmatched=["Definhar Errado"],
    )

    multiline_text = (
        "inv 05004\n"
        "+1 01020\n"
        "- Definhar Errado\n"
        "+2 Shrivelling 3"
    )

    report = parser.parse_and_apply(draft, multiline_text)
    assert report.applied_count == 4
    assert draft.investigator_code == "05004"
    assert draft.cards["01020"] == 2
    assert draft.cards["02066"] == 2
    assert "Definhar Errado" not in draft.unmatched


def test_materialize_draft_builds_valid_matches_and_investigator(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01020": 2},
        unmatched=["AlgumaCartaPerdida"],
        transcribed_text="deck teste",
    )

    matches, inv = parser.materialize_draft(draft)
    assert inv is not None
    assert inv.matched is True
    assert inv.card_code == "05004"
    assert inv.official_name == "Diana Stanley"

    matched_cards = [m for m in matches if m.matched]
    unmatched_cards = [m for m in matches if not m.matched]

    assert len(matched_cards) == 1
    assert matched_cards[0].card_code == "01020"
    assert matched_cards[0].intent.quantidade == 2

    assert len(unmatched_cards) == 1
    assert unmatched_cards[0].intent.nome == "AlgumaCartaPerdida"


def test_format_draft_summary_includes_quick_commands_footer(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01020": 2},
        unmatched=["Unresolved"],
        transcribed_text="meu audio",
    )

    summary = parser.format_draft_summary(draft, feedbacks=["✅ Alteração concluída"])
    assert "✅ Alteração concluída" in summary
    assert "Diana Stanley" in summary
    assert "2x Machete" in summary
    assert "Unresolved" in summary
    assert "Comandos rápidos:" in summary
    assert "`inv <id_ou_nome>`" in summary



def test_draft_deck_parser_bilingual_remove_and_swap(
    parser_and_repo: tuple[DraftDeckCommandParser, ArkhamDBCardRepository],
) -> None:
    """Parser can remove and swap cards using their Portuguese names (e.g. Facão, Definhar)."""
    parser, repo = parser_and_repo
    draft = DraftDeck(
        investigator_code="05004",
        cards={"01020": 2, "01060": 2},  # 2x Machete (Facão), 2x Shrivelling (Definhar)
        unmatched=[],
    )

    # 1. Remove 1 copy of Machete using Portuguese name "Facão"
    report_rem = parser.parse_and_apply(draft, "-1 Facão")
    assert report_rem.applied_count == 1
    assert draft.cards.get("01020") == 1

    # 2. Swap Definhar por Estoque de Emergência
    report_swap = parser.parse_and_apply(draft, "swap Definhar por Estoque de Emergência")
    assert report_swap.applied_count == 1
    assert "01060" not in draft.cards
    assert draft.cards.get("01088") == 2
