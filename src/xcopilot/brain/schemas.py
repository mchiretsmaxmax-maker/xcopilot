"""Схемы «Мозга» (ТЗ v3.1, приложение D): чтение скриншота (этап 7) и разбор материала (7A).

Модель обязана вернуть JSON по этим схемам. Лишние поля запрещены, длины ограничены:
это часть защиты от мусора и инъекций, а не оформление. Извлечение знаний и пакет знаний
добавят этапы 9A и 9B.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Format = Literal["post", "thread", "reply", "quote", "article"]
Element = Literal[
    "hook", "structure", "device", "closer", "tone", "lexicon", "packaging", "timing", "general"
]
TermKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
Lang = Annotated[str, Field(pattern=r"^[a-z]{2}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 1. Скриншот (роль cheap, vision)
class Counter(Strict):
    value: int | None = Field(None, ge=0, description="Число целиком: 12400, а не 12.4K")
    as_shown: str | None = Field(None, max_length=16, description="Как написано на скриншоте")
    confidence: float = Field(ge=0, le=1)


class ScreenshotRead(Strict):
    is_x_post: bool
    kind: Literal["post", "reply", "quote", "thread_part", "profile", "other"]
    author_handle: str | None = Field(None, max_length=32)
    author_followers: Counter | None = None
    text: str = Field(max_length=6000, description="Текст поста дословно, с переносами строк")
    parent_author_handle: str | None = Field(None, max_length=32)
    parent_text: str | None = Field(None, max_length=3000)
    posted_at_shown: str | None = Field(None, max_length=40)
    views: Counter | None = None
    likes: Counter | None = None
    replies: Counter | None = None
    reposts: Counter | None = None
    bookmarks: Counter | None = None
    has_media: bool = False
    unreadable: list[str] = Field(default_factory=list, max_length=5)


# ---------------------------------------------------------------- 2. Разбор материала (роль cheap)
class OutlineStep(Strict):
    role: Literal[
        "hook", "context", "claim", "proof", "example", "turn", "takeaway", "cta", "other"
    ]
    quote: str = Field(min_length=1, max_length=200, description="Дословный фрагмент материала")


class WhyHypothesis(Strict):
    element: Element
    claim: str = Field(min_length=10, max_length=200)
    quote: str = Field(min_length=1, max_length=200)


class DeclaredClaim(Strict):
    statement: str = Field(min_length=15, max_length=240)
    quote: str = Field(min_length=1, max_length=300)


class ProposedTerm(Strict):
    facet: Literal["archetype", "hook_type", "structure", "device", "closer", "tone"]
    key: TermKey
    title: str = Field(min_length=3, max_length=60)
    description: str = Field(min_length=20, max_length=240)
    quote: str = Field(min_length=1, max_length=200)


class MaterialAnalysis(Strict):
    format: Format
    lang: Lang
    archetype: TermKey
    hook_type: TermKey
    hook_text: str = Field(max_length=280)
    structure: TermKey
    outline: list[OutlineStep] = Field(default_factory=list, max_length=8)
    tone: list[TermKey] = Field(default_factory=list, max_length=3)
    devices: list[TermKey] = Field(default_factory=list, max_length=6)
    closer: TermKey
    human_markers: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=4
    )
    ai_markers: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=4
    )
    why: list[WhyHypothesis] = Field(default_factory=list, max_length=3)
    declared_claims: list[DeclaredClaim] = Field(default_factory=list, max_length=8)
    proposed_terms: list[ProposedTerm] = Field(default_factory=list, max_length=2)
    quality_flags: list[
        Literal["truncated", "low_content", "non_text", "parent_missing", "not_content"]
    ] = Field(default_factory=list)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def verify_quotes(analysis: MaterialAnalysis, material_text: str) -> MaterialAnalysis:
    """Код, не модель: выбрасывает всё, чья цитата не найдена в материале дословно."""
    hay = _norm(material_text)

    def ok(quote: str) -> bool:
        return _norm(quote) in hay

    return analysis.model_copy(
        update={
            "hook_text": analysis.hook_text if ok(analysis.hook_text) else "",
            "outline": [s for s in analysis.outline if ok(s.quote)],
            "human_markers": [m for m in analysis.human_markers if ok(m)],
            "ai_markers": [m for m in analysis.ai_markers if ok(m)],
            "why": [w for w in analysis.why if ok(w.quote)],
            "declared_claims": [c for c in analysis.declared_claims if ok(c.quote)],
            "proposed_terms": [t for t in analysis.proposed_terms if ok(t.quote)],
        }
    )
