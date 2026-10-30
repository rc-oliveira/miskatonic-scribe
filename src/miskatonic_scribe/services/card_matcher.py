"""Fuzzy matching service using thefuzz to cross-reference LLM intents and investigators with ArkhamDB."""

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
    InvestigatorMatchResult,
)

logger = logging.getLogger(__name__)


def _normalize_text(value: str) -> str:
    """Normalize card name for consistent fuzzy comparison."""
    lowered = value.lower().strip()
    lowered = lowered.replace("’", "'").replace("“", '"').replace("”", '"')
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


class ArkhamFuzzyCardMatcher(ICardMatcher):
    """Cross-references extracted English card names, investigators, and XP levels with ArkhamDB using thefuzz."""

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

    def match_investigator(self, investigator_name: str) -> InvestigatorMatchResult:
        """Find the best ArkhamDB investigator card matching investigator_name."""
        raw_clean = investigator_name.strip()
        if not raw_clean:
            return InvestigatorMatchResult(raw_name=raw_clean, matched=False, similarity_score=0)

        cards = self._repository.load_cards()
        if not cards:
            return InvestigatorMatchResult(raw_name=raw_clean, matched=False, similarity_score=0)

        investigator_cards = [c for c in cards if c.is_investigator]
        pool = investigator_cards if investigator_cards else list(cards)

        query_norm = _normalize_text(raw_clean)
        scored: List[Tuple[int, ArkhamCardRecord]] = [
            (self._compute_similarity(query_norm, card), card) for card in pool
        ]
        scored.sort(key=lambda item: self._candidate_sort_key(item, 0))

        best_score, best_card = scored[0]
        if best_score < self._threshold:
            logger.info(
                "No investigator match above threshold (%d%%) for '%s'. Best candidate: '%s' (%s) at %d%%.",
                self._threshold,
                raw_clean,
                best_card.real_name,
                best_card.code,
                best_score,
            )
            return InvestigatorMatchResult(
                raw_name=raw_clean,
                matched=False,
                similarity_score=best_score,
            )

        logger.info(
            "Matched investigator '%s' -> '%s' (ID: %s, score: %d%%)",
            raw_clean,
            best_card.real_name,
            best_card.code,
            best_score,
        )
        return InvestigatorMatchResult(
            raw_name=raw_clean,
            matched=True,
            card_code=best_card.code,
            official_name=best_card.real_name,
            subname=best_card.subname,
            faction_code=best_card.faction_code,
            octgn_id=best_card.octgn_id,
            similarity_score=best_score,
        )

    def match_intent(self, intent: ExtractedCardIntent) -> CardMatchResult:
        """Find the best ArkhamDB card ID (code) matching name and XP level."""
        cards = [c for c in self._repository.load_cards() if not c.is_investigator]
        if not cards:
            logger.warning("Card database is empty; cannot match '%s'.", intent.nome)
            return CardMatchResult(intent=intent, matched=False, similarity_score=0)

        query_norm = _normalize_text(intent.nome)
        target_xp = intent.nivel

        exact_xp_scored: List[Tuple[int, ArkhamCardRecord]] = []
        all_scored: List[Tuple[int, ArkhamCardRecord]] = []

        for card in cards:
            score = self._compute_similarity(query_norm, card)
            all_scored.append((score, card))
            if card.xp == target_xp:
                exact_xp_scored.append((score, card))

        exact_xp_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))
        all_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))

        # Smart XP selection:
        # If target_xp == 0 (default when user didn't state XP) and exact_xp has only a weak match (< 88%),
        # while all_scored has a near-exact name match (>= 92% and >= 15 points higher), prefer all_scored!
        # Example: "Counterspell" (XP 2 only) vs "Counterpunch" (XP 0, 67%).
        use_exact_xp = bool(exact_xp_scored and exact_xp_scored[0][0] >= self._threshold)
        if use_exact_xp and target_xp == 0 and all_scored:
            exact_score = exact_xp_scored[0][0]
            any_xp_score = all_scored[0][0]
            if exact_score < 88 and any_xp_score >= 92 and (any_xp_score - exact_score) >= 15:
                logger.info(
                    "Upgrading XP=0 weak match '%s' (%d%%) to exact-name higher-XP card '%s' (ID: %s, XP: %d, %d%%)",
                    exact_xp_scored[0][1].real_name,
                    exact_score,
                    all_scored[0][1].real_name,
                    all_scored[0][1].code,
                    all_scored[0][1].xp,
                    any_xp_score,
                )
                use_exact_xp = False

        chosen_pool = exact_xp_scored if use_exact_xp else all_scored

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
            octgn_id=best_card.octgn_id,
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
