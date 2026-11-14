"""Схема паспорта стиля (pydantic). Форма JSON согласована с этапом 9 (stages-08-09-contracts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Casing = Literal["lower", "any"]
Level = Literal["none", "rare", "free"]

RULE_NAMES = ("casing", "emoji", "hashtags", "links", "max_len")


class HardRules(BaseModel):
    """Жёсткие правила: проверяются кодом и важнее предложений модели."""

    model_config = ConfigDict(extra="forbid")

    casing: Casing = "any"
    emoji: Level = "free"
    hashtags: Level = "free"
    links: Level = "free"
    max_len: int = Field(default=280, ge=1, le=25_000)
    banned_phrases: list[str] = Field(default_factory=list)


class Lexicon(BaseModel):
    use: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class Structure(BaseModel):
    length: str = ""
    line_breaks: str = ""
    openings: list[str] = Field(default_factory=list)
    endings: list[str] = Field(default_factory=list)


class FormatShare(BaseModel):
    format: str
    share: float = Field(ge=0, le=1)


class Example(BaseModel):
    chunk_id: str
    text: str


class Evidence(BaseModel):
    """Утверждение паспорта и куски-доказательства (id кусков своего голоса)."""

    claim: str
    chunk_ids: list[str] = Field(min_length=1)


class StylePassport(BaseModel):
    summary: str = ""
    tone: str = ""
    lexicon: Lexicon = Field(default_factory=Lexicon)
    structure: Structure = Field(default_factory=Structure)
    formats: list[FormatShare] = Field(default_factory=list)
    hard_rules: HardRules = Field(default_factory=HardRules)
    signature_patterns: list[str] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    rough: bool = False
