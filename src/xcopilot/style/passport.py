"""Схема паспорта стиля (pydantic). Форма JSON согласована с этапом 9 (stages-08-09-contracts)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Casing = Literal["lower", "any"]
Level = Literal["none", "rare", "free"]

RULE_NAMES = ("casing", "emoji", "hashtags", "links", "max_len")
# всё, что владелец может задать вручную (owner_set): правила и список запретов
OWNER_RULE_NAMES = (*RULE_NAMES, "banned_phrases")
# служебные поля версии: писателю не нужны, в файл не выгружаются
FORMAT_NAMES = ("post", "thread", "reply", "quote")
# заголовки разделов файла и подписи в боте: «Правила для постов»
FORMAT_TITLES = {
    "post": "постов",
    "thread": "тредов",
    "reply": "ответов",
    "quote": "цитат",
}
META_FIELDS = ("label", "origin", "owner_set", "built_from", "built_at")


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
    # «Мои правила»: свободный текст владельца, писатель получает дословно
    owner_rules: str = ""
    # «Правила для постов/тредов/ответов/цитат»: ключи post|thread|reply|quote, текст владельца
    format_rules: dict[str, str] = Field(default_factory=dict)
    # служебное (META_FIELDS): название версии, откуда она («file», «rebuild», …),
    # какие жёсткие правила задал владелец (их пересборка не трогает)
    label: str = ""
    origin: str = ""
    owner_set: list[str] = Field(default_factory=list)
    # «Чистый голос»: id своих источников без AI-шаблонов, из которых собрана версия, и
    # время автосборки (по нему считается частота автопересборки; ручная — без времени)
    built_from: list[str] = Field(default_factory=list)
    built_at: datetime | None = None
