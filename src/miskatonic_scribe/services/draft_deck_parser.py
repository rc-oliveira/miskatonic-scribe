"""Deterministic command parser for in-memory deck editing (Stateful CRUD)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from miskatonic_scribe.domain.interfaces import ICardMatcher, ICardRepository
from miskatonic_scribe.domain.models import (
    ArkhamCardRecord,
    CardMatchResult,
    DraftDeck,
    ExtractedCardIntent,
    InvestigatorMatchResult,
)

logger = logging.getLogger(__name__)


@dataclass
class DraftDeckCommandReport:
    """Outcome of parsing and applying commands to a DraftDeck."""

    applied_count: int = 0
    feedbacks: List[str] = field(default_factory=list)

    @property
    def has_feedbacks(self) -> bool:
        return bool(self.feedbacks)


class DraftDeckCommandParser:
    """Parses text commands deterministically and mutates a DraftDeck instance without invoking LLMs."""

    def __init__(self, repository: ICardRepository, matcher: ICardMatcher) -> None:
        self._repository = repository
        self._matcher = matcher

    def _get_card_index(self) -> Dict[str, ArkhamCardRecord]:
        """Index all cards by code for fast direct O(1) lookups."""
        cards = self._repository.load_cards()
        return {c.code: c for c in cards}

    def _resolve_card_by_code(self, code: str) -> Optional[ArkhamCardRecord]:
        index = self._get_card_index()
        return index.get(code.strip())

    def _find_investigator_by_code(self, code: str) -> Optional[ArkhamCardRecord]:
        record = self._resolve_card_by_code(code)
        if record and record.is_investigator:
            return record
        return None

    def materialize_draft(
        self, draft: DraftDeck
    ) -> Tuple[List[CardMatchResult], Optional[InvestigatorMatchResult]]:
        """Convert a DraftDeck state into CardMatchResult list and optional InvestigatorMatchResult."""
        index = self._get_card_index()

        investigator_result: Optional[InvestigatorMatchResult] = None
        if draft.investigator_code:
            inv_rec = index.get(draft.investigator_code)
            if inv_rec and inv_rec.is_investigator:
                investigator_result = InvestigatorMatchResult(
                    raw_name=inv_rec.real_name,
                    matched=True,
                    card_code=inv_rec.code,
                    official_name=inv_rec.real_name,
                    subname=inv_rec.subname,
                    faction_code=inv_rec.faction_code,
                    octgn_id=inv_rec.octgn_id,
                    similarity_score=100,
                )
            else:
                investigator_result = InvestigatorMatchResult(
                    raw_name=draft.investigator_code,
                    matched=False,
                    card_code=None,
                    similarity_score=0,
                )

        matches: List[CardMatchResult] = []
        for code, quantity in draft.cards.items():
            record = index.get(code)
            if record:
                matches.append(
                    CardMatchResult(
                        intent=ExtractedCardIntent(
                            nome=record.real_name,
                            nivel=record.xp,
                            quantidade=quantity,
                        ),
                        matched=True,
                        card_code=record.code,
                        official_name=record.real_name,
                        matched_xp=record.xp,
                        subname=record.subname,
                        type_code=record.type_code,
                        octgn_id=record.octgn_id,
                        similarity_score=100,
                    )
                )
            else:
                matches.append(
                    CardMatchResult(
                        intent=ExtractedCardIntent(
                            nome=f"ID {code}",
                            nivel=0,
                            quantidade=quantity,
                        ),
                        matched=True,
                        card_code=code,
                        official_name=f"Carta #{code}",
                        matched_xp=0,
                        similarity_score=100,
                    )
                )

        for term in draft.unmatched:
            clean_term = term.removeprefix("Investigador: ").strip()
            matches.append(
                CardMatchResult(
                    intent=ExtractedCardIntent(
                        nome=clean_term,
                        nivel=0,
                        quantidade=1,
                    ),
                    matched=False,
                    similarity_score=0,
                )
            )

        return matches, investigator_result

    def format_draft_summary(
        self,
        draft: DraftDeck,
        feedbacks: Optional[Sequence[str]] = None,
    ) -> str:
        """Format the Telegram summary representing the current state of DraftDeck."""
        index = self._get_card_index()
        lines: List[str] = []

        if feedbacks:
            feedback_block = "\n".join(feedbacks)
            lines.append(f"{feedback_block}\n")

        if draft.transcribed_text:
            lines.append(f"🎙️ *Entrada:* _{draft.transcribed_text}_\n")

        if draft.investigator_code:
            inv_rec = index.get(draft.investigator_code)
            if inv_rec and inv_rec.is_investigator:
                sub = f" [{inv_rec.subname}]" if inv_rec.subname else ""
                lines.append(f"🕵️ *Investigador:* {inv_rec.real_name}{sub} - ID: {inv_rec.code}\n")
            else:
                lines.append(f"🕵️ *Investigador:* ID: {draft.investigator_code}\n")

        total_copies = draft.total_cards()
        unique_cards = draft.total_unique_cards()
        lines.append(
            f"🃏 *Cartas Identificadas ({total_copies} cópia(s) / {unique_cards} carta(s)):*"
        )

        for code, quantity in draft.cards.items():
            record = index.get(code)
            if record:
                sub = f" [{record.subname}]" if record.subname else ""
                lines.append(
                    f"• {quantity}x {record.real_name}{sub} (Nível {record.xp}) - ID: {record.code}"
                )
            else:
                lines.append(f"• {quantity}x Carta desconhecida - ID: {code}")

        if draft.unmatched:
            lines.append("\n⚠️ *Cartas Não Resolvidas (não encontradas):*")
            for term in draft.unmatched:
                lines.append(f"• {term}")

        lines.append(
            "\n💡 _Comandos rápidos:_\n"
            "• `inv <id_ou_nome>` (trocar investigador)\n"
            "• `+<qtd> <id_ou_nome> [xp]` ou `add <qtd> <id>`\n"
            "• `- <nome_ou_id>` ou `remove <nome_ou_id>`\n"
            "• `swap <de> por <para>` ou `troca <de> por <para>`\n\n"
            "Deseja confirmar o registro destas cartas?"
        )
        return "\n".join(lines)

    def parse_and_apply(self, draft: DraftDeck, text: str) -> DraftDeckCommandReport:
        """Parse commands separated by newlines and apply them to the DraftDeck."""
        report = DraftDeckCommandReport()
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for line in lines:
            self._apply_single_command(draft, line, report)

        return report

    def _apply_single_command(
        self,
        draft: DraftDeck,
        raw_cmd: str,
        report: DraftDeckCommandReport,
    ) -> None:
        """Evaluate a single command string and update the draft."""
        cmd = raw_cmd.strip()
        index = self._get_card_index()

        # 1. Investigador (inv <arg> ou investigador <arg>)
        inv_match = re.match(r"^(?:investigador|inv)\s+(.+)$", cmd, flags=re.IGNORECASE)
        if inv_match:
            arg = inv_match.group(1).strip()
            if re.fullmatch(r"\d{5}", arg):
                record = index.get(arg)
                if record and record.is_investigator:
                    draft.investigator_code = record.code
                    draft.unmatched = [
                        u for u in draft.unmatched if not u.lower().startswith("investigador:")
                    ]
                    report.applied_count += 1
                    report.feedbacks.append(
                        f"✅ Investigador alterado para: *{record.display_name}* (ID: `{record.code}`)"
                    )
                elif record:
                    report.feedbacks.append(
                        f"⚠️ A carta *{record.display_name}* (ID: `{arg}`) não é um investigador."
                    )
                else:
                    report.feedbacks.append(f"⚠️ Código `{arg}` não encontrado no ArkhamDB.")
            else:
                match_res = self._matcher.match_investigator(arg)
                if match_res.matched and match_res.card_code:
                    draft.investigator_code = match_res.card_code
                    draft.unmatched = [
                        u for u in draft.unmatched if not u.lower().startswith("investigador:")
                    ]
                    report.applied_count += 1
                    report.feedbacks.append(
                        f"✅ Investigador alterado para: *{match_res.official_name}* (ID: `{match_res.card_code}`)"
                    )
                else:
                    report.feedbacks.append(f"⚠️ Investigador '{arg}' não encontrado no ArkhamDB.")
            return

        # 2. Swap / Troca (swap|troca|trocar <origem> por <destino> ou swap <arg1> <arg2>)
        swap_match = re.match(r"^(?:swap|troca|trocar)\s+(.+)$", cmd, flags=re.IGNORECASE)
        if swap_match:
            body = swap_match.group(1).strip()
            orig, dest = self._parse_swap_operands(draft, body, index)
            if not orig or not dest:
                report.feedbacks.append(
                    f"⚠️ Não foi possível identificar a origem e o destino na troca: '{cmd}'. "
                    f"Use o formato: `swap <de> por <para>`"
                )
                return

            self._apply_swap(draft, orig, dest, index, report)
            return

        # 3. Remover (-<qtd> <target> ou remove/del/rm [<qtd>] <target>)
        remove_match = (
            re.match(r"^-(?:\s*(\d+))?\s+(.+)$", cmd)
            or re.match(r"^(?:remove|del|rm)(?:\s+(\d+))?\s+(.+)$", cmd, flags=re.IGNORECASE)
        )
        if remove_match:
            qty_str, target = remove_match.groups()
            qty = int(qty_str) if qty_str else None
            self._apply_remove(draft, target.strip(), qty, index, report)
            return

        # 4. Adicionar (+<qtd> <target> ou add [<qtd>] <target>)
        add_match = (
            re.match(r"^\+(?:\s*(\d+))?\s+(.+)$", cmd)
            or re.match(r"^add(?:\s+(\d+))?\s+(.+)$", cmd, flags=re.IGNORECASE)
        )
        if add_match:
            qty_str, target = add_match.groups()
            qty = int(qty_str) if qty_str else 1
            self._apply_add(draft, target.strip(), max(1, qty), index, report)
            return

        report.feedbacks.append(f"⚠️ Comando não reconhecido: '{cmd}'")

    def _apply_add(
        self,
        draft: DraftDeck,
        target: str,
        qty: int,
        index: Dict[str, ArkhamCardRecord],
        report: DraftDeckCommandReport,
    ) -> None:
        """Add a card by direct 5-digit code or fuzzy matching name."""
        if re.fullmatch(r"\d{5}", target):
            record = index.get(target)
            if record:
                draft.cards[target] = draft.cards.get(target, 0) + qty
                self._clear_unmatched_by_record(draft, record)
                report.applied_count += 1
                report.feedbacks.append(
                    f"✅ Adicionado: *{qty}x {record.display_name}* (ID: `{record.code}`)"
                )
            else:
                report.feedbacks.append(f"⚠️ Código `{target}` não encontrado no ArkhamDB.")
            return

        xp = 0
        name_clean = target
        xp_match = re.search(r"^(.*?)(?:\s+(?:nível|level|xp|lvl)?\s*(\d+))?$", target, flags=re.IGNORECASE)
        if xp_match and xp_match.group(2):
            name_clean = xp_match.group(1).strip()
            xp = int(xp_match.group(2))

        intent = ExtractedCardIntent(nome=name_clean, nivel=xp, quantidade=qty)
        match = self._matcher.match_intent(intent, investigator=draft.investigator_code)

        if match.matched and match.card_code:
            draft.cards[match.card_code] = draft.cards.get(match.card_code, 0) + qty
            self._clear_unmatched_by_term(draft, name_clean)
            if match.official_name:
                self._clear_unmatched_by_term(draft, match.official_name)
            report.applied_count += 1
            sub = f" [{match.subname}]" if match.subname else ""
            report.feedbacks.append(
                f"✅ Adicionado: *{qty}x {match.official_name}{sub}* (Nível {match.matched_xp}) - ID: `{match.card_code}`"
            )
        else:
            if name_clean not in draft.unmatched:
                draft.unmatched.append(name_clean)
            report.feedbacks.append(
                f"⚠️ Carta '{name_clean}' não encontrada no ArkhamDB (score: {match.similarity_score}%). Adicionada às não resolvidas."
            )

    def _apply_remove(
        self,
        draft: DraftDeck,
        target: str,
        qty: Optional[int],
        index: Dict[str, ArkhamCardRecord],
        report: DraftDeckCommandReport,
    ) -> None:
        """Remove card(s) by code, name, or from unmatched list."""
        clean_target = target.strip()
        lowered = clean_target.lower()

        # 1. Checa por código numérico exato presente em draft.cards
        if clean_target in draft.cards:
            code = clean_target
            rec = index.get(code)
            display = rec.display_name if rec else f"ID {code}"
            current_qty = draft.cards[code]
            if qty is not None and qty < current_qty:
                draft.cards[code] -= qty
                report.applied_count += 1
                report.feedbacks.append(
                    f"🗑️ Removida(s) {qty} cópia(s) de *{display}* (restam {draft.cards[code]})."
                )
            else:
                del draft.cards[code]
                report.applied_count += 1
                report.feedbacks.append(f"🗑️ Removida(s) todas as cópias de *{display}*.")
            return

        # 2. Checa em unmatched
        for u in list(draft.unmatched):
            if lowered in u.lower() or u.lower() in lowered:
                draft.unmatched.remove(u)
                report.applied_count += 1
                report.feedbacks.append(f"🗑️ Removido item não resolvido: '_{u}_'.")
                return

        # 3. Checa por nome em draft.cards
        matched_codes: List[str] = []
        for code in list(draft.cards.keys()):
            rec = index.get(code)
            if rec:
                names = [rec.real_name.lower(), rec.name.lower()]
                if rec.pt_name:
                    names.append(rec.pt_name.lower())
                if rec.subname:
                    names.append(f"{rec.real_name.lower()} {rec.subname.lower()}")
                if rec.pt_subname and rec.pt_name:
                    names.append(f"{rec.pt_name.lower()} {rec.pt_subname.lower()}")
                if any(lowered in n or n in lowered for n in names):
                    matched_codes.append(code)

        if matched_codes:
            code = matched_codes[0]
            rec = index.get(code)
            display = rec.display_name if rec else f"ID {code}"
            current_qty = draft.cards[code]
            if qty is not None and qty < current_qty:
                draft.cards[code] -= qty
                report.applied_count += 1
                report.feedbacks.append(
                    f"🗑️ Removida(s) {qty} cópia(s) de *{display}* (restam {draft.cards[code]})."
                )
            else:
                del draft.cards[code]
                report.applied_count += 1
                report.feedbacks.append(f"🗑️ Removida(s) todas as cópias de *{display}*.")
            return

        report.feedbacks.append(
            f"⚠️ Carta ou termo '{clean_target}' não encontrado no deck ou na lista de pendências."
        )

    def _parse_swap_operands(
        self,
        draft: DraftDeck,
        body: str,
        index: Dict[str, ArkhamCardRecord],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract origin and destination from a swap expression."""
        delim_match = re.match(
            r"^(.*?)\s+(?:por|for|to|->)\s+(.+)$", body, flags=re.IGNORECASE
        )
        if delim_match:
            return delim_match.group(1).strip(), delim_match.group(2).strip()

        two_codes_match = re.match(r"^(\d{5})\s+(\d{5})$", body)
        if two_codes_match:
            return two_codes_match.group(1), two_codes_match.group(2)

        body_lower = body.lower()
        candidates: List[Tuple[int, str]] = []

        for code in draft.cards.keys():
            if body.startswith(code):
                candidates.append((len(code), code))
            rec = index.get(code)
            if rec:
                name_variants = [rec.real_name, rec.name]
                if rec.pt_name:
                    name_variants.append(rec.pt_name)
                for name_variant in name_variants:
                    if body_lower.startswith(name_variant.lower()):
                        candidates.append((len(name_variant), name_variant))

        for u in draft.unmatched:
            if body_lower.startswith(u.lower()):
                candidates.append((len(u), u))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            best_len, orig_token = candidates[0]
            dest_token = body[best_len:].strip()
            if dest_token:
                return orig_token, dest_token

        parts = body.split()
        if len(parts) >= 2:
            return " ".join(parts[:-1]), parts[-1]

        return None, None

    def _apply_swap(
        self,
        draft: DraftDeck,
        orig: str,
        dest: str,
        index: Dict[str, ArkhamCardRecord],
        report: DraftDeckCommandReport,
    ) -> None:
        """Swap origin item with destination item in equal quantity (or destination specified quantity)."""
        orig_clean = orig.strip()
        orig_lower = orig_clean.lower()
        qty = 1
        orig_display = orig_clean

        found = False
        if orig_clean in draft.cards:
            qty = draft.cards.pop(orig_clean)
            rec = index.get(orig_clean)
            orig_display = rec.display_name if rec else orig_clean
            found = True
        else:
            for u in list(draft.unmatched):
                if orig_lower in u.lower() or u.lower() in orig_lower:
                    draft.unmatched.remove(u)
                    orig_display = u
                    found = True
                    break

            if not found:
                for code in list(draft.cards.keys()):
                    rec = index.get(code)
                    if rec:
                        names = [rec.real_name.lower(), rec.name.lower()]
                        if rec.pt_name:
                            names.append(rec.pt_name.lower())
                        if rec.subname:
                            names.append(f"{rec.real_name.lower()} {rec.subname.lower()}")
                        if rec.pt_subname and rec.pt_name:
                            names.append(f"{rec.pt_name.lower()} {rec.pt_subname.lower()}")
                        if any(orig_lower in n or n in orig_lower for n in names):
                            qty = draft.cards.pop(code)
                            orig_display = rec.display_name
                            found = True
                            break

        if not found:
            report.feedbacks.append(
                f"⚠️ Origem '{orig_clean}' não encontrada no deck para substituição."
            )
            return

        dest_clean = dest.strip()

        # Verifica se o destino possui quantidade explícita no início (ex: '2 Foi só um arranhão nível 2')
        dest_qty_match = re.match(r"^(\d+)\s+(.+)$", dest_clean)
        if dest_qty_match:
            qty = max(1, int(dest_qty_match.group(1)))
            dest_clean = dest_qty_match.group(2).strip()

        if re.fullmatch(r"\d{5}", dest_clean):
            record = index.get(dest_clean)
            if record:
                draft.cards[dest_clean] = draft.cards.get(dest_clean, 0) + qty
                self._clear_unmatched_by_record(draft, record)
                report.applied_count += 1
                report.feedbacks.append(
                    f"🔄 Substituído *{orig_display}* por *{qty}x {record.display_name}* (ID: `{record.code}`)."
                )
            else:
                report.feedbacks.append(
                    f"⚠️ Origem removida, mas código de destino `{dest_clean}` não foi encontrado no ArkhamDB."
                )
            return

        xp = 0
        name_clean = dest_clean
        xp_match = re.search(r"^(.*?)(?:\s+(?:nível|level|xp|lvl)?\s*(\d+))?$", dest_clean, flags=re.IGNORECASE)
        if xp_match and xp_match.group(2):
            name_clean = xp_match.group(1).strip()
            xp = int(xp_match.group(2))

        intent = ExtractedCardIntent(nome=name_clean, nivel=xp, quantidade=qty)
        match = self._matcher.match_intent(intent, investigator=draft.investigator_code)

        if match.matched and match.card_code:
            draft.cards[match.card_code] = draft.cards.get(match.card_code, 0) + qty
            self._clear_unmatched_by_term(draft, name_clean)
            if match.official_name:
                self._clear_unmatched_by_term(draft, match.official_name)
            report.applied_count += 1
            sub = f" [{match.subname}]" if match.subname else ""
            report.feedbacks.append(
                f"🔄 Substituído *{orig_display}* por *{qty}x {match.official_name}{sub}* (Nível {match.matched_xp}) - ID: `{match.card_code}`."
            )
        else:
            if name_clean not in draft.unmatched:
                draft.unmatched.append(name_clean)
            report.feedbacks.append(
                f"🔄 *{orig_display}* removido, mas '{name_clean}' não foi encontrada no ArkhamDB. Adicionada às não resolvidas."
            )

    @staticmethod
    def _clear_unmatched_by_term(draft: DraftDeck, term: str) -> None:
        t_low = term.strip().lower()
        if not t_low:
            return
        draft.unmatched = [
            u for u in draft.unmatched if t_low not in u.lower() and u.lower() not in t_low
        ]

    @staticmethod
    def _clear_unmatched_by_record(draft: DraftDeck, record: ArkhamCardRecord) -> None:
        names = [record.real_name.lower(), record.name.lower()]
        if record.pt_name:
            names.append(record.pt_name.lower())
        draft.unmatched = [
            u
            for u in draft.unmatched
            if not any(n in u.lower() or u.lower() in n for n in names)
        ]
