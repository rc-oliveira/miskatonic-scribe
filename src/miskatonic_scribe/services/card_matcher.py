"""Fuzzy matching service using thefuzz to cross-reference LLM intents with ArkhamDB."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

from thefuzz import fuzz

from miskatonic_scribe.domain.interfaces import ICardMatcher, ICardRepository
from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardAlternative,
    CardMatchResult,
    ExtractedCardIntent,
)

logger = logging.getLogger(__name__)


def _normalize_text(value: str) -> str:
    """Normalize card name for consistent fuzzy comparison."""
    lowered = value.lower().strip()
    # Replace typographic apostrophes and quotes
    lowered = lowered.replace("’", "'").replace("“", '"').replace("”", '"')
    # Collapse non-alphanumeric characters (except spaces) into spaces
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


class ArkhamFuzzyCardMatcher(ICardMatcher):
    """Cross-references extracted English card names and XP levels with ArkhamDB using thefuzz."""

    def __init__(
        self,
        repository: ICardRepository,
        similarity_threshold: int = 65,
    ) -> None:
        self._repository = repository
        self._threshold = similarity_threshold

    @staticmethod
    def _compute_similarity(query_norm: str, candidate: ArkhamCardRecord) -> int:
        """Compute best fuzzy score between normalized query and candidate names."""
        real_norm = _normalize_text(candidate.real_name)
        if query_norm == real_norm:
            return 100

        scores = [
            fuzz.ratio(query_norm, real_norm),
            fuzz.token_sort_ratio(query_norm, real_norm),
        ]

        if candidate.subname:
            full_norm = _normalize_text(f"{candidate.real_name} {candidate.subname}")
            if query_norm == full_norm:
                return 100
            scores.append(fuzz.ratio(query_norm, full_norm))
            scores.append(fuzz.token_set_ratio(query_norm, full_norm))

        if candidate.name and candidate.name != candidate.real_name:
            local_norm = _normalize_text(candidate.name)
            if query_norm == local_norm:
                return 100
            scores.append(fuzz.ratio(query_norm, local_norm))
            scores.append(fuzz.token_sort_ratio(query_norm, local_norm))

        return int(max(scores))

    @staticmethod
    def _candidate_sort_key(item: Tuple[int, ArkhamCardRecord], target_xp: int) -> Tuple[int, int, int, str]:
        """Sort key prioritizing:
        1. Higher similarity score (descending -> negated)
        2. Exact XP match (0 if exact, else absolute XP distance)
        3. Original release over reprint (0 if original, 1 if duplicate_of_code is set)
        4. Lowest card code lexicographically (earliest pack release)
        """
        score, record = item
        xp_diff = abs(record.xp - target_xp)
        reprint_penalty = 1 if record.is_reprint else 0
        return (-score, xp_diff, reprint_penalty, record.code)

    def match_intent(self, intent: ExtractedCardIntent) -> CardMatchResult:
        """Find the best ArkhamDB card ID (code) matching name and XP level."""
        cards = self._repository.load_cards()
        if not cards:
            logger.warning("Card database is empty; cannot match '%s'.", intent.nome)
            return CardMatchResult(intent=intent, matched=False, similarity_score=0)

        query_norm = _normalize_text(intent.nome)
        target_xp = intent.nivel

        # 1. Score all cards sharing the exact requested XP level first
        exact_xp_scored: List[Tuple[int, ArkhamCardRecord]] = []
        all_scored: List[Tuple[int, ArkhamCardRecord]] = []

        for card in cards:
            score = self._compute_similarity(query_norm, card)
            all_scored.append((score, card))
            if card.xp == target_xp:
                exact_xp_scored.append((score, card))

        exact_xp_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))
        all_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))

        # Prefer exact XP match if it meets threshold; otherwise check if another XP version has a strong match
        chosen_pool = exact_xp_scored if (exact_xp_scored and exact_xp_scored[0][0] >= self._threshold) else all_scored

        if not chosen_pool:
            return CardMatchResult(intent=intent, matched=False, similarity_score=0)

        best_score, best_card = chosen_pool[0]

        if best_score < self._threshold:
            logger.info(
                "No match above threshold (%d%%) for '%s' (XP %d). Best candidate: '%s' (%s) at %d%%.",
                self._threshold,
                intent.nome,
                target_xp,
                best_card.real_name,
                best_card.code,
                best_score,
            )
            return CardMatchResult(
                intent=intent,
                matched=False,
                similarity_score=best_score,
            )

        # Collect distinct non-reprint alternatives with the same official name
        # (e.g., different subtitles like Archaic Glyphs or other XP variants if exact XP wasn't found)
        alternatives: List[CardAlternative] = []
        seen_signatures: Dict[Tuple[str, int, str], str] = {
            (best_card.real_name.lower(), best_card.xp, (best_card.subname or "").lower()): best_card.code
        }

        for score, candidate in chosen_pool[1:]:
            if candidate.code == best_card.code or candidate.is_reprint:
                continue
            if candidate.real_name.lower() != best_card.real_name.lower():
                continue
            sig = (candidate.real_name.lower(), candidate.xp, (candidate.subname or "").lower())
            if sig not in seen_signatures:
                seen_signatures[sig] = candidate.code
                alternatives.append(
                    CardAlternative(
                        code=candidate.code,
                        real_name=candidate.real_name,
                        xp=candidate.xp,
                        subname=candidate.subname,
                    )
                )

        logger.info(
            "Matched '%s' (XP %d) -> '%s' (ID: %s, XP: %d, score: %d%%, alternatives: %d)",
            intent.nome,
            target_xp,
            best_card.real_name,
            best_card.code,
            best_card.xp,
            best_score,
            len(alternatives),
        )

        return CardMatchResult(
            intent=intent,
            matched=True,
            card_code=best_card.code,
            official_name=best_card.real_name,
            matched_xp=best_card.xp,
            subname=best_card.subname,
            similarity_score=best_score,
            alternatives=alternatives,
        )

    def match_all(
        self,
        intents: Sequence[ExtractedCardIntent],
        model_used: str | None = None,
    ) -> List[CardMatchResult]:
        """Match a list of extracted intents against ArkhamDB."""
        results: List[CardMatchResult] = []
        for intent in intents:
            match_res = self.match_intent(intent)
            if model_used:
                match_res = replace(match_res, model_used=model_used)
            results.append(match_res)
        return results
