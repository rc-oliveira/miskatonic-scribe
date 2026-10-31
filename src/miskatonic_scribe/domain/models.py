"""Domain entities, value objects, and DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ExtractedCardIntent(BaseModel):
    """Validated representation of a single card extracted by the LLM."""

    nome: str = Field(..., min_length=1, description="Official card name in English")
    nivel: int = Field(default=0, ge=0, le=10, description="Card XP level (0-10)")
    quantidade: int = Field(default=1, ge=1, description="Quantity of copies mentioned")

    @field_validator("nome", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Card name ('nome') must be a non-empty string.")
        return value.strip()

    @field_validator("nivel", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> int:
        if value is None or value == "":
            return 0
        return int(value)

    @field_validator("quantidade", mode="before")
    @classmethod
    def normalize_quantity(cls, value: object) -> int:
        if value is None or value == "":
            return 1
        return max(1, int(value))


class ExtractedDeckPayload(BaseModel):
    """Structured extraction output from Gemini containing optional investigator, cards, and removals."""

    transcricao: Optional[str] = Field(default=None, description="Transcribed text when using direct audio")
    investigador: Optional[str] = Field(default=None, description="Official investigator name in English")
    cartas: List[ExtractedCardIntent] = Field(default_factory=list)
    remover: List[str] = Field(default_factory=list, description="Card names to remove from pending batch")

    @field_validator("investigador", mode="before")
    @classmethod
    def normalize_investigator(cls, value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"null", "none", "n/a", "desconhecido", "unknown"}:
            return None
        return cleaned

    @field_validator("remover", mode="before")
    @classmethod
    def normalize_removals(cls, value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]


@dataclass(frozen=True)
class ArkhamCardRecord:
    """Normalized ArkhamDB card entry used for matching."""

    code: str
    real_name: str
    name: str
    xp: int = 0
    subname: Optional[str] = None
    pack_code: Optional[str] = None
    faction_code: Optional[str] = None
    type_code: Optional[str] = None
    duplicate_of_code: Optional[str] = None
    octgn_id: Optional[str] = None

    @property
    def is_reprint(self) -> bool:
        """Return True if this card entry is a duplicate/reprint of an earlier card."""
        return bool(self.duplicate_of_code)

    @property
    def is_investigator(self) -> bool:
        """Return True if this record represents an investigator card."""
        return (self.type_code or "").lower() == "investigator"

    @property
    def display_name(self) -> str:
        """Return official English name with subtitle if present."""
        if self.subname:
            return f"{self.real_name}: {self.subname}"
        return self.real_name


@dataclass(frozen=True)
class InvestigatorMatchResult:
    """Result of fuzzy-matching an investigator name against ArkhamDB."""

    raw_name: str
    matched: bool
    card_code: Optional[str] = None
    official_name: Optional[str] = None
    subname: Optional[str] = None
    faction_code: Optional[str] = None
    octgn_id: Optional[str] = None
    similarity_score: int = 0

    def format_telegram_line(self) -> str:
        """Format the investigator match line for Telegram messages."""
        if not self.matched or not self.card_code or not self.official_name:
            return (
                f"🕵️ *Investigador:* ⚠️ {self.raw_name} "
                f"(Não encontrado no ArkhamDB - melhor score: {self.similarity_score}%)"
            )
        sub = f" [{self.subname}]" if self.subname else ""
        return f"🕵️ *Investigador:* {self.official_name}{sub} - ID: {self.card_code}"


@dataclass(frozen=True)
class CardAlternative:
    """Represents a distinct card variation (e.g. different subtitle or XP variant)."""

    code: str
    real_name: str
    xp: int
    subname: Optional[str] = None


@dataclass(frozen=True)
class CardMatchResult:
    """Result of fuzzy-matching an ExtractedCardIntent against ArkhamDB."""

    intent: ExtractedCardIntent
    matched: bool
    card_code: Optional[str] = None
    official_name: Optional[str] = None
    matched_xp: Optional[int] = None
    subname: Optional[str] = None
    type_code: Optional[str] = None
    octgn_id: Optional[str] = None
    similarity_score: int = 0
    model_used: Optional[str] = None
    alternatives: List[CardAlternative] = field(default_factory=list)

    def format_telegram_line(self) -> str:
        """Format the matched or unmatched result as a human-readable line for Telegram."""
        if not self.matched or not self.card_code or not self.official_name:
            return (
                f"⚠️ {self.intent.quantidade}x {self.intent.nome} "
                f"(Nível {self.intent.nivel}) - Não encontrada no ArkhamDB "
                f"(melhor score: {self.similarity_score}%)"
            )

        base = (
            f"{self.intent.quantidade}x {self.official_name} "
            f"(Nível {self.matched_xp}) - ID: {self.card_code}"
        )
        if self.subname:
            base += f" [{self.subname}]"

        if self.alternatives:
            alt_parts = [
                f"{alt.code}" + (f" ({alt.subname})" if alt.subname else f" (Nível {alt.xp})")
                for alt in self.alternatives[:3]
            ]
            base += f" | Alternativas: {', '.join(alt_parts)}"

        return base


@dataclass(frozen=True)
class PipelineExecutionResult:
    """Full output of processing a voice/audio or text message through the Scribe pipeline."""

    transcribed_text: str
    model_used: str
    extracted_intents: List[ExtractedCardIntent]
    match_results: List[CardMatchResult]
    investigator_match: Optional[InvestigatorMatchResult] = None
    removed_names: List[str] = field(default_factory=list)
