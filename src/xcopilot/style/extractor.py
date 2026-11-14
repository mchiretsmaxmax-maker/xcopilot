"""Извлечение паспорта: выборка примеров в пределах токен-бюджета и вызов модели `writer`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.gateway import LLMGateway
from xcopilot.style.passport import FormatShare, HardRules, Structure, StylePassport

DEFAULT_BUDGET_TOKENS = 6000
ROUGH_BELOW = 5
PROMPT_KEY = "style.extract.v1"


@dataclass(frozen=True)
class Sample:
    id: UUID
    text: str
    format: str
    tokens: int


class Claim(BaseModel):
    """Утверждение модели с id кусков-доказательств."""

    text: str
    evidence: list[str] = Field(default_factory=list)


class ExtractOut(BaseModel):
    """Ответ модели. Её `hard_rules` игнорируются: правила берутся из статистики."""

    summary: str = ""
    tone: str = ""
    lexicon_use: list[Claim] = Field(default_factory=list)
    lexicon_avoid: list[Claim] = Field(default_factory=list)
    structure: Structure = Field(default_factory=Structure)
    formats: list[FormatShare] = Field(default_factory=list)
    banned_phrases: list[Claim] = Field(default_factory=list)
    signature_patterns: list[Claim] = Field(default_factory=list)
    example_chunk_ids: list[str] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    hard_rules: HardRules | None = None


class StyleExtractor:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        *,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._budget = budget_tokens

    async def sample_examples(self, account_id: UUID, budget_tokens: int) -> list[Sample]:
        raise NotImplementedError

    async def extract(self, account_id: UUID) -> StylePassport:
        raise NotImplementedError
