"""Стенд «Мозга» (этап 7A): настоящая БД, шлюз с подделкой дешёвой модели, FakeX.

Материал создаётся сразу «принятым» (как после ингеста): строка sources + документ.
`analysis_json` собирает ответ модели по схеме MaterialAnalysis с дословными цитатами.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeX
from tests.fakes.llm import FakeLLM
from tests.fakes.x import FakeBudgetGuard
from tests.ingest_stand import RecordingNotifier, build_gateway
from xcopilot.brain.analyze import Analyzer
from xcopilot.brain.history import HistoryImporter
from xcopilot.db.repos import AccountsRepo, SourcesRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.gateway import LLMGateway
from xcopilot.x.client import XClient

SessionFactory = async_sessionmaker[AsyncSession]
OWNER_TG = 700


def analysis_json(material: str, **overrides: Any) -> str:
    """Корректный ответ модели: ключи из стартовых словарей, цитаты — из материала."""
    first = material.strip().splitlines()[0].strip()
    last = material.strip().splitlines()[-1].strip()
    body: dict[str, Any] = {
        "format": "post",
        "lang": "en",
        "archetype": "story",
        "hook_type": "number_personal",
        "hook_text": first,
        "structure": "story_arc",
        "outline": [{"role": "hook", "quote": first}, {"role": "takeaway", "quote": last}],
        "tone": ["vulnerable"],
        "devices": ["specific_number", "short_lines"],
        "closer": "takeaway",
        "human_markers": [first],
        "ai_markers": [],
        "why": [
            {
                "element": "hook",
                "claim": "может работать: цифра и признание в первой строке",
                "quote": first,
            }
        ],
        "declared_claims": [],
        "proposed_terms": [],
        "quality_flags": [],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


@dataclass
class BrainStand:
    sf: SessionFactory
    clock: FakeClock
    account_id: UUID
    user_id: UUID
    llm: FakeLLM
    gateway: LLMGateway
    analyzer: Analyzer
    x: FakeX
    budget: FakeBudgetGuard
    notifier: RecordingNotifier
    importer: HistoryImporter
    created: list[UUID] = field(default_factory=list)

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.sf)

    async def material(
        self,
        body: str,
        *,
        kind: str = "post",
        stance: str = "inspiration",
        origin: str = "manual_text",
        author_handle: str | None = "alice",
        posted_at: datetime | None = None,
        parent_id: UUID | None = None,
        external_id: str | None = None,
        meta: dict[str, Any] | None = None,
        analysis_status: str = "pending",
    ) -> UUID:
        """Принятый материал: sources (status=indexed) + документ с текстом."""
        digest = hashlib.sha256(f"{body}|{uuid4()}".encode()).hexdigest()
        async with self.uow() as uow:
            repo = SourcesRepo(uow.session, self.account_id)
            src, _ = await repo.add_or_get(
                kind=kind,
                stance=stance,
                origin=origin,
                content_hash=digest,
                author_handle=author_handle,
                posted_at=posted_at,
                parent_source_id=parent_id,
                external_id=external_id,
                meta=meta or {},
                status="indexed",
                analysis_status=analysis_status,
            )
            await repo.add_document(src.id, text=body, text_clean=body)
            self.created.append(src.id)
            return src.id

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.sf() as s:
            return list((await s.execute(text(sql), params)).all())

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        return int((await self.rows(f"SELECT count(*) FROM {table} WHERE {where}", **params))[0][0])

    async def exec(self, sql: str, **params: Any) -> None:
        async with self.sf() as s:
            await s.execute(text(sql), params)
            await s.commit()


async def make_brain_stand(
    sf: SessionFactory, clock: FakeClock, *, handle: str | None = None
) -> BrainStand:
    async with UnitOfWork(sf) as uow:
        user, _ = await UsersRepo(uow.session).get_or_create(OWNER_TG, is_admin=True)
        accounts = AccountsRepo(uow.session)
        acc = await accounts.create(
            handle=handle or f"b{uuid4().hex[:8]}", niche="Крипта", lang="en", tz="UTC"
        )
        await accounts.add_member(acc.id, user.id, role="owner")
        account_id, user_id = acc.id, user.id
    llm = FakeLLM(name="deepseek", clock=clock)
    gateway = build_gateway(sf, clock, llm)
    notifier = RecordingNotifier()
    x = FakeX()
    budget = FakeBudgetGuard()

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(sf)

    async def x_for(_: UUID) -> XClient:
        return x

    analyzer = Analyzer(uow_factory, gateway, clock, notifier=notifier)
    importer = HistoryImporter(uow_factory, x_for, budget, clock)
    return BrainStand(
        sf=sf,
        clock=clock,
        account_id=account_id,
        user_id=user_id,
        llm=llm,
        gateway=gateway,
        analyzer=analyzer,
        x=x,
        budget=budget,
        notifier=notifier,
        importer=importer,
    )
