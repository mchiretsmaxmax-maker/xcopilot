"""Темы: предложение, дедуп, ручная тема (ТЗ, этап 10, задачи 1–2).

Вход модели: ниша, теги свежих материалов (14 дней), свои последние посты, рабочие знания.
Дедуп — косинус эмбеддинга заголовка: тема ближе 0.85 к теме за 30 дней (или к уже взятой
в этом ответе) отбрасывается. Ручная тема пишется сразу, без модели и без дедупа.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import timedelta
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, TopicsRepo
from xcopilot.db.repos.writer import WriterRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.context_pack import KnowledgeProvider, TaskSpec
from xcopilot.writer.render import render_knowledge
from xcopilot.writer.schemas import Topic, TopicIdea, TopicsOut
from xcopilot.writer.similarity import TOPIC_MAX, cosine

PROMPT_KEY = "writer.topics.v1"
DEDUPE_DAYS = 30
FRESH_DAYS = 14
OWN_POSTS = 10
TAGS_MAX = 30
EXTRA_IDEAS = 3  # просим больше, чтобы после дедупа осталось n


def dedupe_topics(
    ideas: Sequence[TopicIdea],
    vectors: Sequence[Sequence[float]],
    recent: Sequence[Sequence[float]],
    threshold: float = TOPIC_MAX,
) -> list[int]:
    """Индексы тем, которые остаются: не ближе threshold к недавним и к уже оставленным."""
    kept: list[int] = []
    for i, vec in enumerate(vectors[: len(ideas)]):
        pool = [*recent, *(vectors[j] for j in kept)]
        if all(cosine(vec, other) <= threshold for other in pool):
            kept.append(i)
    return kept


class TopicService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        embedder: Embedder,
        clock: Clock,
        knowledge: KnowledgeProvider | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._embedder = embedder
        self._clock = clock
        self._knowledge = knowledge

    async def suggest_topics(self, account_id: UUID, n: int = 5) -> list[Topic]:
        """n новых тем с углом, обоснованием и форматом; записываются в `topics`."""
        async with self._uow() as uow:
            variables = await self._variables(uow.session, account_id, n + EXTRA_IDEAS)
        out = await self._gateway.generate(
            role=Role.WRITER,
            prompt_key=PROMPT_KEY,
            variables=variables,
            schema=TopicsOut,
            account_id=account_id,
            purpose="writer.topics",
        )
        ideas = out.topics
        vectors = await self._embed([i.title for i in ideas])
        now = self._clock.now()
        async with self._uow() as uow:
            recent = await self._recent_vectors(uow.session, account_id)
            keep = dedupe_topics(ideas, vectors, recent)[:n]
            repo = TopicsRepo(uow.session, account_id)
            topics: list[Topic] = []
            for i in keep:
                idea = ideas[i]
                row = await repo.add(
                    title=idea.title,
                    angle=idea.angle,
                    origin="suggested",
                    dedupe_vec=vectors[i],
                    created_at=now,
                )
                await emit(
                    uow.session,
                    account_id=account_id,
                    type=EventType.TOPIC_SUGGESTED,
                    entity=("topic", row.id),
                    props={"origin": "suggested"},
                )
                topics.append(
                    Topic(
                        id=row.id,
                        title=idea.title,
                        angle=idea.angle,
                        rationale=idea.rationale,
                        format=idea.format,
                        origin="suggested",
                    )
                )
        return topics

    async def add_manual_topic(
        self, account_id: UUID, title: str, *, angle: str | None = None, fmt: str = "post"
    ) -> Topic:
        """Тема владельца: без модели и без дедупа (вектор пишется для будущих дедупов)."""
        title = title.strip()
        if len(title) < 3:
            raise ValueError("тема слишком короткая")
        vector = (await self._embed([title]))[0]
        async with self._uow() as uow:
            row = await TopicsRepo(uow.session, account_id).add(
                title=title,
                angle=angle,
                origin="manual",
                dedupe_vec=vector,
                created_at=self._clock.now(),
            )
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.TOPIC_SUGGESTED,
                entity=("topic", row.id),
                props={"origin": "manual"},
            )
        return Topic(id=row.id, title=title, angle=angle, format=_fmt(fmt), origin="manual")

    # ── внутреннее ──────────────────────────────────────────────────────────────────────

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embedder.embed, texts)

    async def _recent_vectors(self, session: DbSession, account_id: UUID) -> list[list[float]]:
        since = self._clock.now() - timedelta(days=DEDUPE_DAYS)
        return await WriterRepo(session, account_id).topic_vectors_since(since)

    async def _variables(self, session: DbSession, account_id: UUID, n: int) -> dict[str, object]:
        account = await AccountsRepo(session).get(account_id)
        niche = (account.niche if account else None) or "не указана"
        lang = account.lang if account else "ru"
        posts = await self._own_posts(session, account_id)
        own = "\n".join(wrap_untrusted(p, f"own_post_{i}") for i, p in enumerate(posts, 1))
        knowledge = ""
        if self._knowledge is not None:
            task = TaskSpec(format="post", lang=lang, niche=niche, topic=f"темы для ниши {niche}")
            items = await self._knowledge.select(session, account_id, task)
            knowledge = render_knowledge(task, items)
        tags = await self._fresh_tags(session, account_id)
        return {
            "niche": niche,
            "lang": lang,
            "tags": ", ".join(tags) or "нет",
            "knowledge": knowledge,
            "own_posts": own or "пока нет",
            "n": n,
        }

    async def _own_posts(self, session: DbSession, account_id: UUID) -> list[str]:
        return await WriterRepo(session, account_id).own_posts(OWN_POSTS)

    async def _fresh_tags(self, session: DbSession, account_id: UUID) -> list[str]:
        since = self._clock.now() - timedelta(days=FRESH_DAYS)
        return await WriterRepo(session, account_id).fresh_tags(
            since, prefix="topic:", limit=TAGS_MAX
        )


def _fmt(value: str) -> str:
    return value if value in ("post", "thread", "reply", "quote") else "post"
