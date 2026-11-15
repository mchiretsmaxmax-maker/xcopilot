"""Стенд тестов «Мозга» (этап 9A): материалы с разбором и исходом, знания, подделка моделей.

Разборы вставляются строками `material_analyses` напрямую (этап 7A не импортируется), модели —
`FakeBrainLLM`: writer отдаёт заготовленные `ExtractionResult`, судья отвечает функцией.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from xcopilot.brain.context import BrainContext
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.authors import AuthorsRepo
from xcopilot.db.repos.chunks import EmbeddingModelsRepo
from xcopilot.db.repos.knowledge import KnowledgeEmbeddingsRepo, KnowledgeItemsRepo
from xcopilot.db.repos.materials import MaterialAnalysesRepo, MaterialOutcomesRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import Answer, ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA, Verdict

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
TIERS = ("hit", "above", "normal", "below", "flop", "unknown")

JudgeFn = Callable[[str, Mapping[str, Question]], Mapping[str, Answer]]


def answer_yes(p: float = 0.9) -> JudgeFn:
    """Судья: «да» с вероятностью p на noul, первый вариант на choice, верх шкалы на score."""

    def fn(_: str, questions: Mapping[str, Question]) -> dict[str, Answer]:
        out: dict[str, Answer] = {}
        for name, q in questions.items():
            if isinstance(q, NoulQ):
                out[name] = NoulA(p=p)
            elif isinstance(q, ChoiceQ):
                first = next(iter(q.options))
                out[name] = ChoiceA(choice=first, confidence=p, probabilities={first: p})
            else:
                top = len(q.levels) - 1
                out[name] = ScoreA(score=float(top), confidence=p, probabilities={top: p})
        return out

    return fn


def candidate_judge(
    *,
    craft: float = 0.9,
    portable: float = 0.9,
    layer: str = "craft",
    layer_conf: float = 0.9,
    relation: str = "different",
    relation_conf: float = 0.9,
    allowed: float = 1.0,
    natural: float = 2.0,
    rubric: float = 0.9,
) -> JudgeFn:
    """Судья для извлечения: ответы по именам вопросов 9A."""

    def fn(_: str, questions: Mapping[str, Question]) -> dict[str, Answer]:
        out: dict[str, Answer] = {}
        for name in questions:
            if name == "is_craft_claim":
                out[name] = NoulA(p=craft)
            elif name == "portable":
                out[name] = NoulA(p=portable)
            elif name == "layer":
                out[name] = ChoiceA(
                    choice=layer, confidence=layer_conf, probabilities={layer: layer_conf}
                )
            elif name == "relation":
                out[name] = ChoiceA(
                    choice=relation,
                    confidence=relation_conf,
                    probabilities={relation: relation_conf},
                )
            elif name == "allowed":
                out[name] = NoulA(p=allowed)
            elif name == "natural":
                out[name] = ScoreA(
                    score=natural, confidence=0.9, probabilities={round(natural): 0.9}
                )
            else:
                out[name] = NoulA(p=rubric)
        return out

    return fn


@dataclass
class GenCall:
    prompt_key: str
    variables: dict[str, Any]
    system: str
    user: str
    role: Role


@dataclass
class JudgeCall:
    state: str
    questions: dict[str, Question]
    purpose: str


@dataclass
class FakeBrainLLM:
    """Подделка шлюза для «Мозга»: промпты рендерятся настоящим реестром."""

    results: list[BaseModel | Exception] = field(default_factory=list)
    judge: JudgeFn = field(default_factory=answer_yes)
    prompts: PromptRegistry = field(default_factory=PromptRegistry)
    gen_calls: list[GenCall] = field(default_factory=list)
    judge_calls: list[JudgeCall] = field(default_factory=list)

    def push(self, *items: BaseModel | Exception) -> FakeBrainLLM:
        self.results.extend(items)
        return self

    async def generate[T: BaseModel](
        self,
        *,
        role: Role,
        prompt_key: str,
        variables: Mapping[str, Any],
        schema: type[T],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> T:
        rendered = self.prompts.render(prompt_key, variables)
        self.gen_calls.append(
            GenCall(
                prompt_key,
                dict(variables),
                "\n".join(b.text for b in rendered.system),
                rendered.user,
                role,
            )
        )
        if not self.results:
            raise AssertionError("FakeBrainLLM: сценарий writer закончился")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item.model_dump())

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict:
        self.judge_calls.append(JudgeCall(state, dict(questions), purpose))
        return Verdict(answers=dict(self.judge(state, questions)), judge="fake")

    @property
    def rubric_questions(self) -> int:
        return sum(len(c.questions) for c in self.judge_calls if c.purpose == "brain.detect")


def make_ctx(
    session: AsyncSession,
    account_id: UUID,
    *,
    llm: FakeBrainLLM | None = None,
    clock: FakeClock | None = None,
) -> BrainContext:
    return BrainContext(
        session=session,
        account_id=account_id,
        clock=clock or FakeClock(NOW),
        llm=llm or FakeBrainLLM(),
        embedder=FakeEmbedder(),
    )


async def new_account(session: AsyncSession, *, niche: str | None = "crypto") -> UUID:
    acc = await AccountsRepo(session).create(handle=f"a{uuid4().hex[:10]}", tz="UTC", niche=niche)
    return acc.id


async def new_author(
    session: AsyncSession, acc: UUID, *, niche: str | None = "crypto", **extra: Any
) -> UUID:
    a = await AuthorsRepo(session, acc).add(handle=f"au{uuid4().hex[:8]}", niche=niche, **extra)
    return a.id


def analysis_fields(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "post",
        "archetype": "insight",
        "lang": "ru",
        "hook_type": "number",
        "hook_text": "",
        "structure": "list",
        "closer": "question",
        "tone": ["calm"],
        "devices": ["contrast"],
        "features": {
            "char_len": 240,
            "line_count": 4,
            "has_number_first_line": True,
            "has_question": True,
            "emoji_count": 0,
        },
    }
    base.update(overrides)
    return base


async def material(
    session: AsyncSession,
    acc: UUID,
    *,
    stance: str = "inspiration",
    kind: str = "post",
    origin: str = "screenshot",
    author_id: UUID | None = None,
    text: str = "12 провалов за год. Вот что я понял. А вы?",
    tier: str | dict[str, str] | None = "hit",
    signal_origin: str = "screenshot",
    outcome_basis: str = "rates",
    pct_engagement: float | None = None,
    posted_at: datetime = NOW,
    analysis: dict[str, Any] | None = None,
    declared_claims: list[dict[str, str]] | None = None,
) -> UUID:
    """Материал с текстом, текущим разбором и (если tier не None) текущим исходом."""
    src = await SourcesRepo(session, acc).add(
        kind=kind,
        stance=stance,
        origin=origin,
        content_hash=uuid4().hex,
        author_id=author_id,
        posted_at=posted_at,
        analysis_status="analyzed",
        status="indexed",
    )
    await SourcesRepo(session, acc).add_document(src.id, text=text)
    fields = analysis or analysis_fields(
        format=kind if kind in ("post", "thread", "reply", "quote", "article") else "post"
    )
    if declared_claims is not None:
        fields = {**fields, "declared_claims": declared_claims}
    await MaterialAnalysesRepo(session, acc).add_current(
        src.id,
        analyzer_version="brain.analyze_material.v1",
        taxonomy_version=1,
        model="fake",
        **fields,
    )
    if tier is not None:
        tiers = (
            {g: tier for g in ("reach", "engagement", "replies", "saves")}
            if isinstance(tier, str)
            else tier
        )
        await MaterialOutcomesRepo(session, acc).add_current(
            src.id,
            method_version=1,
            basis=outcome_basis,
            signal_origin=signal_origin,
            reliability=0.7,
            pct_engagement=pct_engagement,
            **{f"tier_{g}": t for g, t in tiers.items()},
        )
    return src.id


FEATURE_NUMBER_HOOK: dict[str, Any] = {
    "type": "feature",
    "all": [{"path": "features.has_number_first_line", "op": "is_true"}],
}
RUBRIC_PERSONAL: dict[str, Any] = {
    "type": "rubric",
    "on": "first_line",
    "question": "Первая строка содержит личную цифру автора",
}


async def knowledge(
    session: AsyncSession,
    acc: UUID,
    *,
    statement: str = "Личная цифра в первой строке даёт больше ответов",
    guidance: str = "Начни с личной цифры",
    layer: str = "craft",
    kind: str = "thesis",
    element: str = "hook",
    target_metric: str = "engagement",
    risk_class: str | None = None,
    status: str = "candidate",
    detector: dict[str, Any] | None = None,
    origin: str = "materials",
    embed: bool = False,
    **extra: Any,
) -> UUID:
    it = await KnowledgeItemsRepo(session, acc).create(
        statement=statement,
        guidance=guidance,
        layer=layer,
        kind=kind,
        element=element,
        target_metric=target_metric,
        risk_class=risk_class or {"voice": "A", "humanness": "C"}.get(layer, "B"),
        status=status,
        detector=detector or FEATURE_NUMBER_HOOK,
        origin=origin,
        **extra,
    )
    if embed:
        await embed_item(session, acc, it.id, f"{statement} {guidance}")
    return it.id


async def embed_item(session: AsyncSession, acc: UUID, item_id: UUID, text: str) -> None:
    model = await EmbeddingModelsRepo(session).active()
    assert model is not None
    vec = FakeEmbedder().embed([text])[0]
    await KnowledgeEmbeddingsRepo(session, acc).upsert(item_id, model.id, version=1, vec=vec)
