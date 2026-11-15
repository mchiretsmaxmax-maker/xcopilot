"""Фоновые задачи «Мозга»: brain.detect, brain.extract, brain.nightly.

Каждая задача — одна транзакция (UnitOfWork). `brain.detect` ставится после разбора и исхода
материала (payload {"source_id"}); `brain.extract` — по расписанию или по накоплению разборов;
`brain.nightly` — раз в сутки: пересчёт со спадом, переходы, разбор конфликтов, снимок дня.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.brain import extract, lifecycle, merge, snapshots
from xcopilot.brain.config import BrainConfig, load_brain_config
from xcopilot.brain.context import BrainContext, BrainLLM, NoEmbedder, NoLLM
from xcopilot.brain.evidence import record_from_material
from xcopilot.brain.scoring import recompute
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job

DETECT_KIND = "brain.detect"
EXTRACT_KIND = "brain.extract"
NIGHTLY_KIND = "brain.nightly"


@dataclass
class BrainDeps:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    llm: BrainLLM = field(default_factory=NoLLM)
    embedder: Embedder = field(default_factory=NoEmbedder)
    config: BrainConfig = field(default_factory=load_brain_config)

    def ctx(self, session: DbSession, account_id: UUID) -> BrainContext:
        return BrainContext(
            session=session,
            account_id=account_id,
            clock=self.clock,
            llm=self.llm,
            embedder=self.embedder,
            config=self.config,
        )


async def handle_detect(job: Job, *, deps: BrainDeps) -> int:
    """payload {"source_id"}: детекторы и доказательства (свой пост — own_outcome)."""
    source_id = UUID(str(job.payload["source_id"]))
    async with deps.uow_factory() as uow:
        ctx = deps.ctx(uow.session, job.account_id)
        written = await record_from_material(ctx, source_id)
        if written:
            await recompute(ctx)
        return written


async def handle_extract(job: Job, *, deps: BrainDeps) -> int:
    """Извлечение по всем новым пачкам аккаунта; вернуть число затронутых знаний."""
    async with deps.uow_factory() as uow:
        return len(await extract.run(deps.ctx(uow.session, job.account_id)))


async def handle_nightly(job: Job, *, deps: BrainDeps) -> int:
    """Пересчёт со спадом, переходы, разбор конфликтов, снимок дня; вернуть число переходов."""
    async with deps.uow_factory() as uow:
        account = await AccountsRepo(uow.session).get(job.account_id)
        if account is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        ctx = deps.ctx(uow.session, job.account_id)
        now = deps.clock.now()
        await recompute(ctx, now=now)
        done = await lifecycle.run(ctx, now)
        if await merge.resolve_conflicts(ctx):
            await recompute(ctx, now=now)
        day = now.astimezone(ZoneInfo(account.tz or "UTC")).date()
        await snapshots.take(ctx, day)
        return len(done)
