"""Fuzzy matching service using thefuzz to cross-reference LLM intents and investigators with ArkhamDB."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

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

    @classmethod
    def _card_matches_deck_option(cls, card: ArkhamCardRecord, opt: dict[str, Any]) -> bool:
        """Return True if `card` satisfies all criteria in a single ArkhamDB `deck_options` rule."""
        if "option_select" in opt and isinstance(opt["option_select"], list):
            return any(
                cls._card_matches_deck_option(card, sub)
                for sub in opt["option_select"]
                if isinstance(sub, dict)
            )

        if "not" in opt and isinstance(opt["not"], dict):
            if cls._card_matches_deck_option(card, opt["not"]):
                return False

        allowed_factions = opt.get("faction") or opt.get("faction_select")
        if isinstance(allowed_factions, list) and allowed_factions:
            allowed_set = {str(f).strip().lower() for f in allowed_factions if f}
            if not any(cf in allowed_set for cf in card.factions):
                return False

        level_cfg = opt.get("level")
        if isinstance(level_cfg, dict):
            min_lvl = int(level_cfg.get("min", 0))
            max_lvl = int(level_cfg.get("max", 5))
            if not (min_lvl <= card.xp <= max_lvl):
                return False

        allowed_traits = opt.get("trait")
        if isinstance(allowed_traits, list) and allowed_traits:
            trait_set = {str(t).strip().lower().rstrip(".") for t in allowed_traits if t}
            if not any(ct in trait_set for ct in card.normalized_traits):
                return False

        allowed_types = opt.get("type")
        if isinstance(allowed_types, list) and allowed_types:
            type_set = {str(t).strip().lower() for t in allowed_types if t}
            if (card.type_code or "").strip().lower() not in type_set:
                return False

        allowed_uses = opt.get("uses")
        if isinstance(allowed_uses, list) and allowed_uses:
            text_lower = (card.real_text or "").lower()
            if "uses (" not in text_lower or not any(
                str(u).strip().lower() in text_lower for u in allowed_uses if u
            ):
                return False

        text_patterns = opt.get("text")
        if isinstance(text_patterns, list) and text_patterns:
            card_text = card.real_text or ""
            if not any(
                re.search(str(pat), card_text, flags=re.IGNORECASE)
                for pat in text_patterns
                if pat
            ):
                return False

        allowed_slots = opt.get("slot")
        if isinstance(allowed_slots, list) and allowed_slots:
            card_slots = {
                s.strip().lower() for s in (card.real_slot or "").split(".") if s.strip()
            }
            slot_set = {str(s).strip().lower() for s in allowed_slots if s}
            if not any(cs in slot_set for cs in card_slots):
                return False

        if "permanent" in opt and opt["permanent"] is not None:
            if bool(card.permanent) != bool(opt["permanent"]):
                return False

        return True

    @classmethod
    def is_card_legal_for_investigator(
        cls,
        card: ArkhamCardRecord,
        investigator: ArkhamCardRecord,
    ) -> bool:
        """Evaluate whether `card` can legally be included in `investigator`'s deck per ArkhamDB rules."""
        inv_codes = {investigator.code}
        if investigator.duplicate_of_code:
            inv_codes.add(investigator.duplicate_of_code)

        # 1. Investigator-specific signature cards
        if card.restrictions_investigators:
            return bool(inv_codes.intersection(card.restrictions_investigators))

        if card.code in investigator.deck_requirements_cards or (
            card.duplicate_of_code and card.duplicate_of_code in investigator.deck_requirements_cards
        ):
            return True

        # 2. If investigator has no deck_options metadata, do not restrict
        if not investigator.deck_options:
            return True

        # 3. Evaluate deck_options (negative 'not: true' rules disqualify immediately; must match >= 1 positive rule)
        matched_positive = False
        for opt in investigator.deck_options:
            is_negated = opt.get("not") is True
            opt_criteria = {k: v for k, v in opt.items() if k != "not"} if is_negated else opt
            if cls._card_matches_deck_option(card, opt_criteria):
                if is_negated:
                    return False
                matched_positive = True

        return matched_positive

    def _resolve_investigator_record(
        self,
        investigator: Optional[Union[InvestigatorMatchResult, ArkhamCardRecord, str]],
        all_cards: Sequence[ArkhamCardRecord],
    ) -> Optional[ArkhamCardRecord]:
        """Resolve an InvestigatorMatchResult, ArkhamCardRecord, or name/code string to an ArkhamCardRecord."""
        if investigator is None:
            return None
        if isinstance(investigator, ArkhamCardRecord):
            return investigator

        inv_cards = [c for c in all_cards if c.is_investigator]
        if isinstance(investigator, InvestigatorMatchResult):
            if not investigator.matched:
                return None
            if investigator.card_code:
                for c in inv_cards:
                    if c.code == investigator.card_code:
                        return c
            if investigator.official_name:
                target_norm = _normalize_text(investigator.official_name)
                for c in inv_cards:
                    if _normalize_text(c.real_name) == target_norm:
                        return c
            return None

        if isinstance(investigator, str) and investigator.strip():
            raw = investigator.strip()
            for c in inv_cards:
                if c.code == raw:
                    return c
            match_res = self.match_investigator(raw)
            if match_res.matched and match_res.card_code:
                for c in inv_cards:
                    if c.code == match_res.card_code:
                        return c
        return None

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

    def match_intent(
        self,
        intent: ExtractedCardIntent,
        investigator: Optional[Union[InvestigatorMatchResult, ArkhamCardRecord, str]] = None,
    ) -> CardMatchResult:
        """Find the best ArkhamDB card ID (code) matching name, XP level, and investigator deckbuilding rules."""
        all_records = self._repository.load_cards()
        all_player_cards = [c for c in all_records if not c.is_investigator]
        if not all_player_cards:
            logger.warning("Card database is empty; cannot match '%s'.", intent.nome)
            return CardMatchResult(intent=intent, matched=False, similarity_score=0)

        query_norm = _normalize_text(intent.nome)
        target_xp = intent.nivel

        inv_record = self._resolve_investigator_record(investigator, all_records)
        if inv_record is not None and inv_record.deck_options:
            legal_cards = [
                c for c in all_player_cards if self.is_card_legal_for_investigator(c, inv_record)
            ]
            cards = legal_cards if legal_cards else all_player_cards
        else:
            cards = all_player_cards

        exact_xp_scored: List[Tuple[int, ArkhamCardRecord]] = []
        all_scored: List[Tuple[int, ArkhamCardRecord]] = []

        for card in cards:
            score = self._compute_similarity(query_norm, card)
            all_scored.append((score, card))
            if card.xp == target_xp:
                exact_xp_scored.append((score, card))

        exact_xp_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))
        all_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))

        # Guard against substituting a random weak legal card when the user explicitly named an off-class card
        if inv_record is not None and inv_record.deck_options and all_scored:
            best_legal_score, best_legal_card = all_scored[0]
            if best_legal_score < 80:
                illegal_scored = [
                    (self._compute_similarity(query_norm, c), c)
                    for c in all_player_cards
                    if not self.is_card_legal_for_investigator(c, inv_record)
                ]
                illegal_scored.sort(key=lambda item: self._candidate_sort_key(item, target_xp))
                if illegal_scored and illegal_scored[0][0] >= 92:
                    illegal_score, illegal_card = illegal_scored[0]
                    logger.info(
                        "Card '%s' matched '%s' (ID: %s, %d%%) which is illegal for investigator '%s' (%s); "
                        "rejecting weak legal fallback '%s' (%d%%).",
                        intent.nome,
                        illegal_card.real_name,
                        illegal_card.code,
                        illegal_score,
                        inv_record.real_name,
                        inv_record.code,
                        best_legal_card.real_name,
                        best_legal_score,
                    )
                    return CardMatchResult(
                        intent=intent,
                        matched=False,
                        similarity_score=best_legal_score,
                    )

        # Smart XP selection:
        # When target_xp == 0 (default when user didn't state XP) and a higher-XP card has a strictly
        # better name match (e.g. "I've had worse..." at 100% XP 2 vs "You've had worse..." at 87% XP 0,
        # or "Counterspell" at 100% XP 2 vs "Counterpunch" at 67% XP 0), prefer the higher-XP card!
        use_exact_xp = bool(exact_xp_scored and exact_xp_scored[0][0] >= self._threshold)
        if use_exact_xp and target_xp == 0 and all_scored:
            exact_score = exact_xp_scored[0][0]
            any_xp_score = all_scored[0][0]
            if any_xp_score > exact_score and (
                any_xp_score >= 95
                or (exact_score < 90 and any_xp_score >= 90 and (any_xp_score - exact_score) >= 8)
            ):
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
            type_code=best_card.type_code,
            octgn_id=best_card.octgn_id,
            similarity_score=best_score,
            alternatives=alternatives,
        )

    def match_all(
        self,
        intents: Sequence[ExtractedCardIntent],
        investigator: Optional[Union[InvestigatorMatchResult, ArkhamCardRecord, str]] = None,
        model_used: str | None = None,
    ) -> List[CardMatchResult]:
        """Match a list of extracted intents against ArkhamDB, enforcing investigator deckbuilding rules if present."""
        results: List[CardMatchResult] = []
        for intent in intents:
            match_res = self.match_intent(intent, investigator=investigator)
            if model_used:
                match_res = replace(match_res, model_used=model_used)
            results.append(match_res)
        return results
