"""Tests for JsonUserCollectionRepository."""

from __future__ import annotations

from pathlib import Path

from miskatonic_scribe.domain.models import CardMatchResult, ExtractedCardIntent
from miskatonic_scribe.infrastructure.user_collection_repository import (
    JsonUserCollectionRepository,
)


def test_user_collection_persists_and_accumulates_quantities(tmp_path: Path) -> None:
    db_file = tmp_path / "data" / "user_collections.json"
    repo = JsonUserCollectionRepository(db_path=db_file)

    match_shrivelling = CardMatchResult(
        intent=ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=2),
        matched=True,
        card_code="02066",
        official_name="Shrivelling",
        matched_xp=3,
        similarity_score=100,
    )
    match_unmatched = CardMatchResult(
        intent=ExtractedCardIntent(nome="Unknown Card", nivel=0, quantidade=1),
        matched=False,
        similarity_score=20,
    )

    added = repo.register_cards(
        user_id=42,
        username="investigator_rex",
        matches=[match_shrivelling, match_unmatched],
    )
    assert added == 2

    # Confirming another copy of 02066 increments quantity to 3
    match_more = CardMatchResult(
        intent=ExtractedCardIntent(nome="Shrivelling", nivel=3, quantidade=1),
        matched=True,
        card_code="02066",
        official_name="Shrivelling",
        matched_xp=3,
        similarity_score=100,
    )
    repo.register_cards(user_id=42, username="investigator_rex", matches=[match_more])

    collection = repo.get_user_collection(user_id=42)
    assert len(collection) == 1
    assert collection[0]["code"] == "02066"
    assert collection[0]["quantity"] == 3
    assert collection[0]["xp"] == 3
