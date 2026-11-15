"""Стенд писателя (этап 10): настоящая БД, шлюз с подделками, пакет, знания и испытуемое знание."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeNotifier
from tests.fakes.llm import FakeJudge, FakeLLM, TextCall
from tests.retrieval_stand import FakeKnowledge, Kb, ScriptedEmbedder
from xcopilot.db.repos.materials import MaterialAnalysesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PACKAGED_DIR, PromptRegistry
from xcopilot.llm.resilience import RetryPolicy
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec
from xcopilot.writer.drafts import WriterService
from xcopilot.writer.topics import TopicService

SF = async_sessionmaker[AsyncSession]
LEGACY_MODELS = Path(__file__).parent / "fixtures" / "models_anthropic_jev.toml"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

OWN_POSTS = [
    "блобы сделали l2 дешевле в десять раз",
    "комиссии упали, а пользователи остались",
    "ждём апгрейд сети, без паники",
    "роллапы съедают рынок тихо",
    "газ в сети дешевеет, это хороший знак",
    "не гонись за хайпом, читай документацию",
]

# три лексически разных варианта (попарное сходство rapidfuzz заметно ниже 0.8)
DISTINCT = [
    "блобы снизили комиссии роллапов, и это видно по моим транзакциям за неделю",
    "все ждут чуда от апгрейда, а я просто перенёс кошелёк на l2 и забыл про газ",
    "три вещи, которые я понял о дешёвых сетях: тише, быстрее, скучнее",
]
EXTRA = "старый мост закрылся без шума, зато ликвидность ушла в новые сети за сутки"


async def _no_sleep(_: float) -> None:
    return None


def draft_reply(*texts: str, parts: list[list[str]] | None = None) -> str:
    """JSON-ответ писателя (`DraftBatch`) с вариантами по порядку."""
    variants: list[dict[str, Any]] = []
    for i, body in enumerate(texts):
        v: dict[str, Any] = {"approach": f"заход {i + 1}", "text": body}
        if parts is not None:
            v["parts"] = parts[i]
        variants.append(v)
    return json.dumps({"variants": variants}, ensure_ascii=False)


def topics_reply(*titles: str, fmt: str = "post") -> str:
    """JSON-ответ модели тем (`TopicsOut`)."""
    topics = [
        {"title": t, "angle": f"угол: {t}", "rationale": f"почему: {t}", "format": fmt}
        for t in titles
    ]
    return json.dumps({"topics": topics}, ensure_ascii=False)


def full_prompt(call: TextCall) -> str:
    """Весь текст запроса к модели: системные блоки и сообщения по порядку."""
    system = "\n".join(b.text for b in call.request.system)
    return system + "\n" + "\n".join(m.content for m in call.request.messages)


@dataclass
class FakeProbation:
    """ProbationPicker-подделка: отдаёт заданное испытуемое знание."""

    item: KnowledgeItem | None = None
    tasks: list[TaskSpec] = field(default_factory=list)

    async def pick(self, session: Any, account_id: UUID, task: TaskSpec) -> KnowledgeItem | None:
        self.tasks.append(task)
        return self.item


@dataclass
class WriterStand:
    sf: SF
    clock: FakeClock
    kb: Kb
    account_id: UUID
    writer: FakeLLM
    cheap: FakeLLM
    judge: FakeJudge
    embedder: ScriptedEmbedder
    knowledge: FakeKnowledge
    probation: FakeProbation
    notifier: FakeNotifier
    gateway: LLMGateway
    service: WriterService
    topics: TopicService
    queue: JobQueue

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.sf)

    def prompt(self, i: int = -1) -> str:
        return full_prompt(self.writer.calls[i])

    async def knowledge_item(self, guidance: str, *, slot: str = "core") -> KnowledgeItem:
        """Знание в knowledge_items (для FK) и его вид в пакете."""
        item_id = await self.kb.lesson(self.account_id, guidance)
        return KnowledgeItem(
            item_id=item_id, slot=slot, guidance=guidance, statement=f"Наблюдение: {guidance}"
        )

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.sf() as s:
            return list((await s.execute(text(sql), params)).all())

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        rows = await self.rows(f"SELECT count(*) FROM {table} WHERE {where}", **params)
        return int(rows[0][0])

    async def foreign_post(
        self, body: str, *, url: str = "https://x.com/someone/status/1", analysis: bool = True
    ) -> UUID:
        """Чужой пост (источник + документ) и, если нужно, его текущий разбор (этап 7A)."""
        digest = hashlib.sha256(body.encode()).hexdigest()
        async with self.sf() as s:
            src = (
                await s.execute(
                    text(
                        "INSERT INTO sources (account_id, kind, stance, origin, content_hash,"
                        " status, url, author_handle) VALUES (:a, 'post', 'competitor',"
                        " 'link_oembed', :h, 'indexed', :u, 'someone') RETURNING id"
                    ),
                    {"a": self.account_id, "h": digest, "u": url},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO documents (account_id, source_id, text, text_clean)"
                    " VALUES (:a, :s, :t, :t)"
                ),
                {"a": self.account_id, "s": src, "t": body},
            )
            await s.commit()
        if analysis:
            async with self.uow() as uow:
                await MaterialAnalysesRepo(uow.session, self.account_id).add_current(
                    src,
                    analyzer_version="brain.analyze.v1",
                    taxonomy_version=1,
                    model="deepseek-flash",
                    format="post",
                    lang="ru",
                    hook_type="number_first",
                    hook_text=body.split(".")[0],
                    structure="claim_proof_takeaway",
                    devices=["personal_experience", "single_takeaway"],
                    closer="takeaway",
                    why_hypotheses=[
                        {"claim": "короткий зачин с цифрой цепляет", "quote": body[:20]},
                    ],
                )
        return src  # type: ignore[no-any-return]


def _gateway(sf: SF, clock: FakeClock, writer: FakeLLM, cheap: FakeLLM, judge: FakeJudge,
             prompts: PromptRegistry, day_limit: int) -> LLMGateway:  # fmt: skip
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    return LLMGateway(
        models=ModelsConfig.load(LEGACY_MODELS),
        pricing=PricingTable.load(),
        providers={"anthropic": writer, "deepseek": cheap, "jev": judge},
        prompts=prompts,
        budget=BudgetGuard(uow, clock, default_day_limits={"llm": day_limit, "x_api": 1_000_000}),
        uow_factory=uow,
        clock=clock,
        retry=RetryPolicy(),
        sleep=_no_sleep,
    )


async def make_writer_stand(
    sf: SF,
    *,
    clock: FakeClock | None = None,
    day_limit: int = 50_000_000,
    passport_version: int = 1,
    own_posts: list[str] | None = None,
    handle: str = "writer_acc",
) -> WriterStand:
    """Аккаунт с паспортом и своими постами, шлюз с подделками, сервисы писателя."""
    clock = clock or FakeClock(NOW)
    embedder = ScriptedEmbedder()
    kb = Kb(sf, embedder)
    account_id = await kb.account(handle)
    await kb.passport(account_id, version=passport_version)
    for post in OWN_POSTS if own_posts is None else own_posts:
        await kb.chunk(account_id, post, created_at=NOW - timedelta(days=1))
    prompts = PromptRegistry(PACKAGED_DIR)
    async with UnitOfWork(sf) as u:
        await prompts.sync(u.session)
    writer = FakeLLM(name="anthropic", clock=clock)
    cheap = FakeLLM(name="deepseek")
    judge = FakeJudge(name="jev")
    gateway = _gateway(sf, clock, writer, cheap, judge, prompts, day_limit)
    knowledge = FakeKnowledge()
    probation = FakeProbation()

    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    service = WriterService(
        uow_factory=uow,
        gateway=gateway,
        embedder=embedder,
        clock=clock,
        knowledge=knowledge,
        probation=probation,
        prompts=prompts,
    )
    topics = TopicService(
        uow_factory=uow, gateway=gateway, embedder=embedder, clock=clock, knowledge=knowledge
    )
    return WriterStand(
        sf=sf,
        clock=clock,
        kb=kb,
        account_id=account_id,
        writer=writer,
        cheap=cheap,
        judge=judge,
        embedder=embedder,
        knowledge=knowledge,
        probation=probation,
        notifier=FakeNotifier(),
        gateway=gateway,
        service=service,
        topics=topics,
        queue=JobQueue(uow, clock),
    )


def post_task(topic: str = "дешёвые комиссии в l2", fmt: str = "post", **kw: Any) -> TaskSpec:
    return TaskSpec(format=fmt, lang="ru", niche="Крипта", topic=topic, **kw)
