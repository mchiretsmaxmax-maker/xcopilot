"""Схемы «Мозга» (ТЗ v3.1, приложение D): чтение скриншота (этап 7) и разбор материала (7A).

Модель обязана вернуть JSON по этим схемам. Лишние поля запрещены, длины ограничены:
это часть защиты от мусора и инъекций, а не оформление. Извлечение знаний и пакет знаний
добавят этапы 9A и 9B.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

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


# ================================================ 9A: извлечение знаний (приложение D, часть 3)
Goal = Literal["reach", "engagement", "replies", "saves"]
TargetMetric = Literal["reach", "engagement", "replies", "saves", "quality"]
Layer = Literal["voice", "humanness", "craft", "niche", "platform"]
Kind = Literal["thesis", "hook_pattern", "structure", "anti_pattern", "lexicon", "exemplar"]

_FORBIDDEN = re.compile(r"(https?://|www\.|@\w{2,}|\$[A-Za-z]{2,})")
_TO_MODEL = re.compile(
    r"(ignore (all|any|previous)|system prompt|as an ai|игнорируй (все|предыдущие)"
    r"|системн\w+ промпт|ты — ?ии)",
    re.IGNORECASE,
)


def clean_knowledge_text(value: str) -> str:
    """Знание — утверждение о ремесле: без ссылок, хэндлов, тикеров и обращений к модели."""
    if _FORBIDDEN.search(value):
        raise ValueError("knowledge text must not contain links, handles or tickers")
    if _TO_MODEL.search(value):
        raise ValueError("knowledge text must not address the model")
    return " ".join(value.split())


ALLOWED_PATHS = frozenset(
    {
        "format",
        "archetype",
        "hook_type",
        "structure",
        "closer",
        "devices",
        "tone",
        "features.char_len",
        "features.line_count",
        "features.first_line_len",
        "features.has_number_first_line",
        "features.has_question",
        "features.has_question_last_line",
        "features.first_person",
        "features.emoji_count",
        "features.hashtag_count",
        "features.has_link",
        "features.has_media",
        "features.lowercase_ratio",
        "features.thread_parts",
    }
)


class FeatureCondition(Strict):
    path: str
    op: Literal["eq", "in", "contains", "gte", "lte", "is_true", "is_false"]
    value: str | int | float | list[str] | None = None

    @field_validator("path")
    @classmethod
    def _known_path(cls, v: str) -> str:
        if v not in ALLOWED_PATHS:
            raise ValueError(f"unknown detector path: {v}")
        return v


class FeatureDetector(Strict):
    type: Literal["feature"]
    all: list[FeatureCondition] = Field(min_length=1, max_length=3)


class RubricDetector(Strict):
    type: Literal["rubric"]
    on: Literal["first_line", "full_text"]
    question: str = Field(
        min_length=20,
        max_length=200,
        description="Утверждение о тексте, на которое судья отвечает да или нет",
    )

    _clean = field_validator("question")(clean_knowledge_text)


class NoDetector(Strict):
    type: Literal["none"]


Detector = Annotated[FeatureDetector | RubricDetector | NoDetector, Field(discriminator="type")]
DETECTOR: TypeAdapter[FeatureDetector | RubricDetector | NoDetector] = TypeAdapter(Detector)


class Scope(Strict):
    formats: list[Format] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    niches: list[TermKey] = Field(default_factory=list)
    langs: list[Lang] = Field(default_factory=list)
    archetypes: list[TermKey] = Field(default_factory=list)


MaterialLabel = Annotated[str, Field(pattern=r"^M\d{1,3}$")]


class KnowledgeCandidate(Strict):
    kind: Kind
    layer: Layer
    element: Element
    statement: str = Field(min_length=15, max_length=240)
    guidance: str = Field(min_length=10, max_length=200)
    skeleton: str | None = Field(None, max_length=600)
    target_metric: TargetMetric
    detector: Detector
    scope: Scope = Field(default_factory=Scope)
    supporting: list[MaterialLabel] = Field(min_length=1, max_length=25)
    contradicting: list[MaterialLabel] = Field(default_factory=list, max_length=25)
    same_as_existing: Annotated[str, Field(pattern=r"^K\d{1,3}$")] | None = None
    rationale: str = Field(min_length=10, max_length=300)

    _clean_statement = field_validator("statement", "guidance")(clean_knowledge_text)

    @model_validator(mode="after")
    def _consistency(self) -> KnowledgeCandidate:
        if self.kind == "lexicon" and self.layer != "voice":
            raise ValueError("lexicon knowledge belongs to the voice layer")
        if self.layer in ("voice", "humanness") and self.target_metric != "quality":
            raise ValueError("voice and humanness knowledge has target_metric = quality")
        if self.kind in ("structure", "exemplar") and not self.skeleton:
            raise ValueError("structure and exemplar need a skeleton")
        if set(self.supporting) & set(self.contradicting):
            raise ValueError("a material cannot both support and contradict")
        return self


class ExtractionResult(Strict):
    candidates: list[KnowledgeCandidate] = Field(default_factory=list, max_length=12)
    proposed_terms: list[ProposedTerm] = Field(default_factory=list, max_length=5)
    notes: str | None = Field(None, max_length=400)


class PackItem(Strict):
    """Знание в пакете (контракт для любой модели; собирает этап 9B)."""

    item_id: UUID
    version: int
    slot: Literal["pinned", "core", "probation", "anti"]
    rank: int = Field(ge=1)
    kind: Kind
    layer: Layer
    element: Element
    guidance: str
    statement: str
    skeleton: str | None = None
    confidence: Literal["high", "medium", "low"]
    proven_on_own_posts: bool
    score: float = Field(ge=0)


class TaskSpec(Strict):
    """Описание задачи для пакета знаний (контракт v1; единственный на весь проект).

    `format`, `goal`, `niche`, `archetype` — строки: формат проверяет писатель
    (`WriterInputError`, этап 10), нишу аккаунт хранит как ввёл владелец. Область знаний
    сравнивается по значению без учёта регистра; неизвестное значение просто ничему не
    соответствует.
    """

    format: str = Field(min_length=1, max_length=20)
    goal: str = Field("engagement", min_length=1, max_length=20)
    lang: Lang
    niche: str | None = Field(None, max_length=40)
    archetype: str | None = Field(None, max_length=40)
    topic: str = Field(min_length=3, max_length=500)
    budget_tokens: int = Field(1500, ge=300, le=4000)
    include_probation: bool = True


class KnowledgePack(Strict):
    """Пакет знаний, не зависящий от модели (схема v1)."""

    schema_version: Literal[1] = 1
    pack_id: UUID
    account_id: UUID
    task: TaskSpec
    taxonomy_version: int
    items: list[PackItem]
    token_count: int = Field(ge=0)
    built_at: datetime
    warnings: list[str] = Field(default_factory=list)


class ItemCard(Strict):
    """Карточка знания для бота, экспорта и аналитики (представление v_knowledge_cards)."""

    item_id: UUID
    version: int
    kind: Kind
    layer: Layer
    element: Element
    statement: str
    guidance: str
    skeleton: str | None = None
    status: str
    owner_state: str
    confidence: Literal["high", "medium", "low"]
    scopes: dict[str, list[str]] = Field(default_factory=dict)
    evidence_n: int = 0
    own_support: int = 0
    own_contra: int = 0
    open_conflicts: int = 0


class ExplainedItem(Strict):
    item_id: UUID
    version: int | None
    slot: str
    rank: int
    score: float | None
    statement: str | None = None


class PackExplanation(Strict):
    pack_id: UUID
    consumer: str
    task: TaskSpec
    taxonomy_version: int
    token_count: int
    items: list[ExplainedItem]
