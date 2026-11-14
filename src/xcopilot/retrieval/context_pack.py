"""Контекстный пакет писателя (ТЗ v3.1, 4.2): паспорт, примеры голоса, знания, факты, антипримеры.

Чужой текст в пакет не попадает: только свои посты (примеры) и свои отклонённые черновики
(антипримеры). Знания приходят через `KnowledgeProvider` (настоящий — этап 9B).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.db.repos import FactsRepo, PassportsRepo
from xcopilot.db.repos.retrieval import RetrievalRepo
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.search import Hit, search
from xcopilot.retrieval.tokens import estimate_tokens

DEFAULT_BUDGET = 6000
MIN_EXAMPLES = 4
EXAMPLES_K = 10
GOOD_EXAMPLES = 6
ANTI_EXAMPLES_K = 2
FACTS_MAX = 10
SECTION_CAPS = {"examples": 1600, "knowledge": 1500, "facts": 800, "anti_examples": 300}
"""Потолки секций (токены); паспорт 700, задача 600 и запас 500 не урезаются отдельно."""


class TaskSpec(BaseModel):
    """Задача для пакета (совместимо с контрактом `TaskSpec` этапа 9A, версия схемы 1)."""

    format: str
    goal: str = "engagement"
    lang: str
    niche: str | None = None
    archetype: str | None = None
    topic: str = Field(min_length=3, max_length=500)
    budget_tokens: int = Field(1500, ge=300, le=4000)
    include_probation: bool = True


class KnowledgeItem(BaseModel):
    """Одно знание в пакете (без цифр уверенности: модель переносит их в пост)."""

    item_id: UUID
    slot: str
    guidance: str
    statement: str
    skeleton: str | None = None


class KnowledgeProvider(Protocol):
    """Источник знаний под задачу. Настоящий — `brain.api` (этап 9B)."""

    async def select(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> list[KnowledgeItem]: ...

    async def by_ids(
        self, session: DbSession, account_id: UUID, ids: Sequence[UUID]
    ) -> list[KnowledgeItem]: ...


class NoKnowledge:
    """Пустая заглушка до этапа 9B."""

    async def select(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> list[KnowledgeItem]:
        return []

    async def by_ids(
        self, session: DbSession, account_id: UUID, ids: Sequence[UUID]
    ) -> list[KnowledgeItem]:
        return []


class PackChunk(BaseModel):
    chunk_id: UUID
    kind: str
    text: str


class PackAnti(BaseModel):
    """Свой отклонённый черновик."""

    draft_id: UUID
    text: str


class PackFact(BaseModel):
    fact_id: UUID
    statement: str
    value: str | None = None
    unit: str | None = None
    source_url: str


class PackIds(BaseModel):
    """То, что хранится в `generation_runs.context_pack`: по этому можно восстановить пакет."""

    task: TaskSpec
    budget_tokens: int
    passport_id: UUID | None = None
    passport_version: int | None = None
    examples: list[UUID] = Field(default_factory=list)
    knowledge: list[UUID] = Field(default_factory=list)
    facts: list[UUID] = Field(default_factory=list)
    anti_examples: list[UUID] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)


_PASSPORT_SEPARATE = ("hard_rules", "anti_examples")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(items, start=1))


def _fact_line(f: PackFact) -> str:
    value = f" ({f.value}{' ' + f.unit if f.unit else ''})" if f.value else ""
    return f"- {f.statement}{value} — источник: {f.source_url}"


def _knowledge_line(k: KnowledgeItem) -> str:
    line = f"- [{k.slot}] {k.guidance} — {k.statement}"
    return line + (f"\n  Каркас: {k.skeleton}" if k.skeleton else "")


class ContextPack(BaseModel):
    task: TaskSpec
    budget_tokens: int
    tokens: int = 0
    passport_id: UUID | None = None
    passport_version: int | None = None
    passport: dict[str, Any] | None = None
    hard_rules: Any = None
    examples: list[PackChunk] = Field(default_factory=list)
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    facts: list[PackFact] = Field(default_factory=list)
    anti_examples: list[PackAnti] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)

    def to_ids(self) -> PackIds:
        """Только id и счётчики — это кладут в `generation_runs.context_pack`."""
        return PackIds(
            task=self.task,
            budget_tokens=self.budget_tokens,
            passport_id=self.passport_id,
            passport_version=self.passport_version,
            examples=[c.chunk_id for c in self.examples],
            knowledge=[k.item_id for k in self.knowledge],
            facts=[f.fact_id for f in self.facts],
            anti_examples=[a.draft_id for a in self.anti_examples],
            warnings=list(self.warnings),
            trimmed=dict(self.trimmed),
        )

    def sections(self) -> list[tuple[str, str]]:
        """Секции текста в порядке показа модели; пустые пропускаются."""
        t = self.task
        out = [
            (
                "task",
                f"# Задача\nТема: {t.topic}\nФормат: {t.format}\nЯзык: {t.lang}\nЦель: {t.goal}",
            )
        ]
        if self.passport is not None:
            body = {k: v for k, v in self.passport.items() if k not in _PASSPORT_SEPARATE}
            text = f"# Паспорт стиля (v{self.passport_version})\n{_dump(body)}"
            if self.hard_rules not in (None, {}, []):
                text += f"\n\n# Жёсткие правила\n{_dump(self.hard_rules)}"
            out.append(("passport", text))
        if self.examples:
            body_ = _numbered([c.text for c in self.examples])
            out.append(("examples", "# Примеры голоса\n" + body_))
        if self.knowledge:
            lines = "\n".join(_knowledge_line(k) for k in self.knowledge)
            out.append(("knowledge", "# Знания\n" + lines))
        if self.facts:
            out.append(("facts", "# Факты\n" + "\n".join(_fact_line(f) for f in self.facts)))
        if self.anti_examples:
            items = _numbered([a.text for a in self.anti_examples])
            out.append(("anti_examples", "# Антипримеры (так не писать)\n" + items))
        return out

    def render(self) -> str:
        """Текст для промпта писателя. Детерминирован; зависит только от содержимого секций."""
        return "\n\n".join(text for _, text in self.sections())

    def section_tokens(self) -> dict[str, int]:
        """Токены каждой секции (для проверки бюджета по секциям)."""
        return {name: estimate_tokens(text) for name, text in self.sections()}


def _apply_passport(pack: ContextPack, passport: Any) -> None:
    """Положить в пакет паспорт и жёсткие правила."""
    body = dict(passport.body)
    pack.passport_id, pack.passport_version, pack.passport = passport.id, passport.version, body
    pack.hard_rules = body.get("hard_rules", passport.hard_rules)


def _chunks(hits: list[Hit]) -> list[PackChunk]:
    return [PackChunk(chunk_id=h.chunk_id, kind=h.kind, text=h.text) for h in hits]


def _facts(rows: Any) -> list[PackFact]:
    return [
        PackFact(
            fact_id=f.id,
            statement=f.statement,
            value=f.value,
            unit=f.unit,
            source_url=f.source_url,
        )
        for f in rows
    ]


def _finish(pack: ContextPack) -> ContextPack:
    pack.tokens = estimate_tokens(pack.render())
    return pack


def _cut(pack: ContextPack, section: str, items: list[Any]) -> None:
    items.pop()
    pack.trimmed[section] = pack.trimmed.get(section, 0) + 1


def _trim(pack: ContextPack) -> None:
    """Урезать пакет.

    1) каждая секция — до своего потолка (хвост уходит первым); знания — ещё и до
       `task.budget_tokens`; 2) общий бюджет: антипримеры → знания → факты → примеры (до 4).
    Паспорт, жёсткие правила и задача не урезаются.
    """
    caps = dict(SECTION_CAPS)
    caps["knowledge"] = min(caps["knowledge"], pack.task.budget_tokens)
    lists: dict[str, list[Any]] = {
        "examples": pack.examples,
        "knowledge": pack.knowledge,
        "facts": pack.facts,
        "anti_examples": pack.anti_examples,
    }
    floors = {"examples": MIN_EXAMPLES, "knowledge": 0, "facts": 0, "anti_examples": 0}
    for name, items in lists.items():
        while len(items) > floors[name] and pack.section_tokens().get(name, 0) > caps[name]:
            _cut(pack, name, items)

    def over() -> bool:
        return estimate_tokens(pack.render()) > pack.budget_tokens

    while over():
        for name in ("anti_examples", "knowledge", "facts", "examples"):
            if len(lists[name]) > floors[name]:
                _cut(pack, name, lists[name])
                break
        else:
            pack.warnings.append("over_budget")
            return


async def build_pack(
    session: DbSession,
    account_id: UUID,
    task: TaskSpec,
    *,
    embedder: Embedder,
    now: datetime,
    knowledge: KnowledgeProvider | None = None,
    budget_tokens: int = DEFAULT_BUDGET,
) -> ContextPack:
    """Собрать пакет под задачу в рамках бюджета (по умолчанию 6000 токенов).

    Один и тот же запрос к одному состоянию базы даёт один и тот же пакет.
    """
    warnings: list[str] = []
    pack = ContextPack(task=task, budget_tokens=budget_tokens)
    passport = await PassportsRepo(session, account_id).get_active()
    if passport is None:
        warnings.append("no_active_passport")
    else:
        _apply_passport(pack, passport)

    examples = await search(
        session, account_id, task.topic, embedder=embedder, k=EXAMPLES_K, own_only=True
    )
    pack.examples = _chunks(examples)
    if not examples:
        warnings.append("empty_knowledge_base")
    elif len(examples) < GOOD_EXAMPLES:
        warnings.append("few_examples")

    provider = knowledge or NoKnowledge()
    pack.knowledge = await provider.select(session, account_id, task)

    facts = sorted(
        await FactsRepo(session, account_id).list_valid(now),
        key=lambda f: (-f.observed_at.timestamp(), str(f.id)),
    )
    pack.facts = _facts(facts[:FACTS_MAX])

    repo = RetrievalRepo(session, account_id)
    pack.anti_examples = [
        PackAnti(draft_id=i, text=t) for i, t in await repo.rejected_drafts(ANTI_EXAMPLES_K)
    ]

    pack.warnings = warnings
    _trim(pack)
    _finish(pack)
    await emit(
        session,
        account_id=account_id,
        type=EventType.RETRIEVAL_PACK_BUILT,
        props={
            "topic": task.topic,
            "fmt": task.format,
            "tokens": pack.tokens,
            "budget_tokens": budget_tokens,
            "examples": len(pack.examples),
            "knowledge": len(pack.knowledge),
            "facts": len(pack.facts),
            "anti_examples": len(pack.anti_examples),
            "trimmed": sum(pack.trimmed.values()),
        },
    )
    return pack


async def rehydrate(
    session: DbSession,
    account_id: UUID,
    pack_ids: PackIds,
    *,
    knowledge: KnowledgeProvider | None = None,
) -> ContextPack:
    """Восстановить пакет по сохранённым id: ровно то, что видел писатель.

    Тексты читаются по id (факт — даже если срок уже истёк). Чужие и удалённые пропускаются.
    """
    pack = ContextPack(
        task=pack_ids.task,
        budget_tokens=pack_ids.budget_tokens,
        warnings=list(pack_ids.warnings),
        trimmed=dict(pack_ids.trimmed),
    )
    if pack_ids.passport_id is not None:
        passport = await PassportsRepo(session, account_id).get(pack_ids.passport_id)
        if passport is not None:
            _apply_passport(pack, passport)
    repo = RetrievalRepo(session, account_id)
    pack.examples = _chunks(await repo.chunks_by_ids(pack_ids.examples))
    pack.anti_examples = [
        PackAnti(draft_id=i, text=t) for i, t in await repo.drafts_by_ids(pack_ids.anti_examples)
    ]
    provider = knowledge or NoKnowledge()
    pack.knowledge = await provider.by_ids(session, account_id, pack_ids.knowledge)
    facts = FactsRepo(session, account_id)
    found = [await facts.get(i) for i in pack_ids.facts]
    pack.facts = _facts([f for f in found if f is not None])
    return _finish(pack)
