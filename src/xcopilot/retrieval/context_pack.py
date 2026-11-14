"""Контекстный пакет писателя: паспорт, примеры голоса, уроки, факты, антипримеры, референсы."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.db.repos import AccountsRepo, FactsRepo, LessonsRepo, PassportsRepo
from xcopilot.db.repos.retrieval import RetrievalRepo
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.search import Hit, search
from xcopilot.retrieval.tokens import estimate_tokens

DEFAULT_BUDGET = 6000
VOICE_KINDS = ("own_post",)
REFERENCE_KINDS = ("liked_sample", "competitor_post")
MIN_EXAMPLES = 4
EXAMPLES_K = 12
REFERENCES_K = 5
ANTI_EXAMPLES_K = 2
LESSONS_MAX = 20
FACTS_MAX = 10
GOOD_EXAMPLES = 8


class PackChunk(BaseModel):
    chunk_id: UUID
    kind: str
    text: str


class PackLesson(BaseModel):
    lesson_id: UUID
    kind: str
    text: str
    weight: float
    scope: Literal["account", "global"]


class PackFact(BaseModel):
    fact_id: UUID
    statement: str
    value: str | None = None
    unit: str | None = None
    source_url: str


class PackIds(BaseModel):
    """То, что хранится в `generation_runs.context_pack`: по этому можно восстановить пакет."""

    topic: str
    fmt: str
    budget_tokens: int
    passport_id: UUID | None = None
    passport_version: int | None = None
    examples: list[UUID] = Field(default_factory=list)
    references: list[UUID] = Field(default_factory=list)
    lessons: list[UUID] = Field(default_factory=list)
    facts: list[UUID] = Field(default_factory=list)
    anti_examples: int = 0
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)


class ContextPack(BaseModel):
    topic: str
    fmt: str
    budget_tokens: int
    tokens: int = 0
    passport_id: UUID | None = None
    passport_version: int | None = None
    passport: dict[str, Any] | None = None
    hard_rules: Any = None
    examples: list[PackChunk] = Field(default_factory=list)
    lessons: list[PackLesson] = Field(default_factory=list)
    facts: list[PackFact] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    references: list[PackChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)

    def to_ids(self) -> PackIds:
        """Только id и счётчики — это кладут в `generation_runs.context_pack`."""
        return PackIds(
            topic=self.topic,
            fmt=self.fmt,
            budget_tokens=self.budget_tokens,
            passport_id=self.passport_id,
            passport_version=self.passport_version,
            examples=[c.chunk_id for c in self.examples],
            references=[c.chunk_id for c in self.references],
            lessons=[x.lesson_id for x in self.lessons],
            facts=[f.fact_id for f in self.facts],
            anti_examples=len(self.anti_examples),
            warnings=list(self.warnings),
            trimmed=dict(self.trimmed),
        )

    def render(self) -> str:
        """Текст для промпта писателя. Детерминирован; зависит только от содержимого секций."""
        parts = [f"# Задача\nТема: {self.topic}\nФормат: {self.fmt}"]
        if self.passport is not None:
            body = {k: v for k, v in self.passport.items() if k not in _PASSPORT_SEPARATE}
            parts.append(f"# Паспорт стиля (v{self.passport_version})\n{_dump(body)}")
        if self.hard_rules not in (None, {}, []):
            parts.append(f"# Жёсткие правила\n{_dump(self.hard_rules)}")
        if self.examples:
            parts.append("# Примеры голоса\n" + _numbered([c.text for c in self.examples]))
        if self.lessons:
            parts.append("# Уроки\n" + "\n".join(f"- [{x.kind}] {x.text}" for x in self.lessons))
        if self.facts:
            parts.append("# Факты\n" + "\n".join(_fact_line(f) for f in self.facts))
        if self.anti_examples:
            parts.append("# Антипримеры (так не писать)\n" + _numbered(self.anti_examples))
        if self.references:
            parts.append(
                "# Референсы (чужие посты: тема и фактура, но не голос)\n"
                + _numbered([c.text for c in self.references])
            )
        return "\n\n".join(parts)


_PASSPORT_SEPARATE = ("hard_rules", "anti_examples")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(items, start=1))


def _fact_line(f: PackFact) -> str:
    value = f" ({f.value}{' ' + f.unit if f.unit else ''})" if f.value else ""
    return f"- {f.statement}{value} — источник: {f.source_url}"


def _anti_examples(body: dict[str, Any]) -> list[str]:
    """Антипримеры паспорта как строки (элемент — строка или объект с полем text)."""
    raw = body.get("anti_examples")
    items = raw if isinstance(raw, list) else []
    out: list[str] = []
    for item in items:
        text = item.get("text") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            out.append(text)
    return out


def _apply_passport(pack: ContextPack, passport: Any, anti_limit: int) -> None:
    """Положить в пакет паспорт, жёсткие правила и первые антипримеры."""
    body = dict(passport.body)
    pack.passport_id, pack.passport_version, pack.passport = passport.id, passport.version, body
    pack.hard_rules = body.get("hard_rules", passport.hard_rules)
    pack.anti_examples = _anti_examples(body)[:anti_limit]


def _chunks(hits: list[Hit]) -> list[PackChunk]:
    return [PackChunk(chunk_id=h.chunk_id, kind=h.kind, text=h.text) for h in hits]


def _finish(pack: ContextPack) -> ContextPack:
    pack.tokens = estimate_tokens(pack.render())
    return pack


def _trim(pack: ContextPack) -> None:
    """Урезать до бюджета: антипримеры → референсы → факты → примеры (не меньше четырёх)."""

    def over() -> bool:
        return estimate_tokens(pack.render()) > pack.budget_tokens

    def cut(section: str, items: list[Any]) -> None:
        items.pop()
        pack.trimmed[section] = pack.trimmed.get(section, 0) + 1

    while over():
        if pack.anti_examples:
            cut("anti_examples", pack.anti_examples)
        elif pack.references:
            cut("references", pack.references)
        elif pack.facts:
            cut("facts", pack.facts)
        elif len(pack.examples) > MIN_EXAMPLES:
            cut("examples", pack.examples)
        else:
            pack.warnings.append("over_budget")
            return


async def build_pack(
    session: DbSession,
    account_id: UUID,
    topic: str,
    fmt: str,
    *,
    embedder: Embedder,
    now: datetime,
    budget_tokens: int = DEFAULT_BUDGET,
) -> ContextPack:
    """Собрать пакет по теме: паспорт, примеры, уроки, факты, антипримеры, референсы.

    Паспорт, жёсткие правила и уроки не урезаются. Остальное — в рамках бюджета, в
    фиксированном порядке (см. `_trim`). Один запрос к одному состоянию базы — один пакет.
    """
    warnings: list[str] = []
    passport = await PassportsRepo(session, account_id).get_active()
    pack = ContextPack(topic=topic, fmt=fmt, budget_tokens=budget_tokens)
    if passport is None:
        warnings.append("no_active_passport")
    else:
        _apply_passport(pack, passport, ANTI_EXAMPLES_K)

    examples = await search(
        session, account_id, topic, embedder=embedder, kinds=VOICE_KINDS, k=EXAMPLES_K
    )
    references = await search(
        session, account_id, topic, embedder=embedder, kinds=REFERENCE_KINDS, k=REFERENCES_K
    )
    pack.examples, pack.references = _chunks(examples), _chunks(references)
    if not examples and not references:
        warnings.append("empty_knowledge_base")
    elif len(examples) < GOOD_EXAMPLES:
        warnings.append("few_examples")

    own = await LessonsRepo(session, account_id).list_by_status("accepted")
    global_id = (await AccountsRepo(session).get_global()).id
    shared = await LessonsRepo(session, global_id).list_by_status("accepted")
    lessons = [(x, "account") for x in own] + [(x, "global") for x in shared]
    lessons.sort(key=lambda p: (-p[0].weight, str(p[0].id)))
    pack.lessons = [
        PackLesson(lesson_id=x.id, kind=x.kind, text=x.text, weight=x.weight, scope=scope)
        for x, scope in lessons[:LESSONS_MAX]
    ]

    facts = sorted(
        await FactsRepo(session, account_id).list_valid(now),
        key=lambda f: (-f.observed_at.timestamp(), str(f.id)),
    )
    pack.facts = [
        PackFact(
            fact_id=f.id,
            statement=f.statement,
            value=f.value,
            unit=f.unit,
            source_url=f.source_url,
        )
        for f in facts[:FACTS_MAX]
    ]

    pack.warnings = warnings
    _trim(pack)
    _finish(pack)
    await emit(
        session,
        account_id=account_id,
        type=EventType.RETRIEVAL_PACK_BUILT,
        props={
            "topic": topic,
            "fmt": fmt,
            "tokens": pack.tokens,
            "budget_tokens": budget_tokens,
            "examples": len(pack.examples),
            "lessons": len(pack.lessons),
            "facts": len(pack.facts),
            "references": len(pack.references),
            "anti_examples": len(pack.anti_examples),
            "trimmed": sum(pack.trimmed.values()),
        },
    )
    return pack


async def rehydrate(session: DbSession, account_id: UUID, pack_ids: PackIds) -> ContextPack:
    """Восстановить пакет по сохранённым id: ровно то, что видел писатель.

    Тексты читаются по id (факты — даже если срок уже истёк, уроки — даже если сняты).
    Чужие и удалённые куски пропускаются.
    """
    pack = ContextPack(
        topic=pack_ids.topic,
        fmt=pack_ids.fmt,
        budget_tokens=pack_ids.budget_tokens,
        warnings=list(pack_ids.warnings),
        trimmed=dict(pack_ids.trimmed),
    )
    if pack_ids.passport_id is not None:
        passport = await PassportsRepo(session, account_id).get(pack_ids.passport_id)
        if passport is not None:
            _apply_passport(pack, passport, pack_ids.anti_examples)

    repo = RetrievalRepo(session, account_id)
    pack.examples = _chunks(await repo.chunks_by_ids(pack_ids.examples))
    pack.references = _chunks(await repo.chunks_by_ids(pack_ids.references))

    global_id = (await AccountsRepo(session).get_global()).id
    own, shared = LessonsRepo(session, account_id), LessonsRepo(session, global_id)
    for lesson_id in pack_ids.lessons:
        scope: Literal["account", "global"] = "account"
        lesson = await own.get(lesson_id)
        if lesson is None:
            lesson, scope = await shared.get(lesson_id), "global"
        if lesson is not None:
            pack.lessons.append(
                PackLesson(
                    lesson_id=lesson.id,
                    kind=lesson.kind,
                    text=lesson.text,
                    weight=lesson.weight,
                    scope=scope,
                )
            )

    facts = FactsRepo(session, account_id)
    for fact_id in pack_ids.facts:
        fact = await facts.get(fact_id)
        if fact is not None:
            pack.facts.append(
                PackFact(
                    fact_id=fact.id,
                    statement=fact.statement,
                    value=fact.value,
                    unit=fact.unit,
                    source_url=fact.source_url,
                )
            )
    return _finish(pack)
