"""Стенд ингеста: настоящая БД, шлюз с подделкой дешёвой модели, FakeX, FakeEmbedder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeEmbedder, FakeX
from tests.fakes.llm import FakeJudge, FakeLLM, TextCall
from xcopilot.db.repos import AccountsRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.ingest.blobs import MemoryBlobStore
from xcopilot.ingest.service import (
    FilePayload,
    IngestPayload,
    IngestReceipt,
    IngestService,
    NotifierLike,
)
from xcopilot.ingest.tagger import Tagger
from xcopilot.jobs.handlers import handle_ingest_process
from xcopilot.jobs.queue import JobQueue
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PACKAGED_DIR, PromptRegistry
from xcopilot.llm.providers.base import ProviderResult, TextRequest
from xcopilot.llm.resilience import RetryPolicy
from xcopilot.x.client import XClient
from xcopilot.x.models import XPost

SessionFactory = async_sessionmaker[AsyncSession]
OWNER_TG = 100


@dataclass
class AutoTagLLM(FakeLLM):
    """Дешёвая модель: по сценарию, а без сценария — теги на каждый блок <untrusted>."""

    material: str = "own_post"
    topics: list[str] = field(default_factory=lambda: ["L2"])

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        if self.script:
            return await super().complete(model=model, request=request)
        self.calls.append(TextCall(model=model, request=request))
        n = sum(m.content.count("<untrusted label=") for m in request.messages)
        item = {"material": self.material, "topics": self.topics, "format": "short", "lang": "ru"}
        return ProviderResult(text=json.dumps({"items": [item] * n}), usage=self.usage, model=model)


@dataclass
class RecordingNotifier:
    sent: list[tuple[UUID, str, Any]] = field(default_factory=list)

    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int:
        self.sent.append((account_id, text, keyboard))
        return 1


async def _no_sleep(_: float) -> None:
    return None


def build_gateway(sf: SessionFactory, clock: FakeClock, cheap: FakeLLM) -> LLMGateway:
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    return LLMGateway(
        models=ModelsConfig.load(),
        pricing=PricingTable.load(),
        providers={
            "anthropic": FakeLLM(name="anthropic"),
            "deepseek": cheap,
            "jev": FakeJudge(name="jev"),
        },
        prompts=PromptRegistry(PACKAGED_DIR),
        budget=BudgetGuard(uow, clock, default_day_limits={"llm": 50_000_000, "x_api": 1_000_000}),
        uow_factory=uow,
        clock=clock,
        retry=RetryPolicy(),
        sleep=_no_sleep,
    )


def post(id_: str, body: str, handle: str = "alice") -> XPost:
    return XPost(id=id_, text=body, author_handle=handle, created_at=None, source="oembed")


def buttons(keyboard: Any) -> list[tuple[str, str]]:
    """(подпись, callback_data) всех кнопок inline-клавиатуры."""
    rows = getattr(keyboard, "inline_keyboard", None) or []
    return [(b.text, b.callback_data or "") for row in rows for b in row]


@dataclass
class Stand:
    sf: SessionFactory
    clock: FakeClock
    account_id: UUID
    user_id: UUID
    x: FakeX
    llm: AutoTagLLM
    embedder: FakeEmbedder
    blobs: MemoryBlobStore
    notifier: NotifierLike
    service: IngestService
    queue: JobQueue

    async def accept(
        self,
        text_: str | None = None,
        *,
        file: tuple[str, bytes] | None = None,
        file_name: str | None = None,
        file_size: int | None = None,
    ) -> IngestReceipt:
        payload = IngestPayload(
            text=text_,
            file=FilePayload(*file) if file else None,
            file_name=file_name or (file[0] if file else None),
            file_size=file_size if file_size is not None else (len(file[1]) if file else None),
            owner_tg_id=OWNER_TG,
        )
        return await self.service.accept(self.account_id, self.user_id, payload)

    async def run_jobs(self) -> int:
        """Выполнить все готовые задачи очереди (как воркер); вернуть число выполненных."""
        done = 0
        while jobs := await self.queue.claim("test", 50):
            for job in jobs:
                try:
                    await handle_ingest_process(job, service=self.service)
                except Exception as exc:
                    await self.queue.fail(job.id, repr(exc))
                else:
                    await self.queue.complete(job.id)
                    done += 1
        return done

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.sf() as s:
            return list((await s.execute(text(sql), params)).all())

    async def count(self, table: str, where: str = "true") -> int:
        return int((await self.rows(f"SELECT count(*) FROM {table} WHERE {where}"))[0][0])

    async def snapshot(self) -> dict[str, int]:
        tables = ("sources", "documents", "chunks", "embeddings", "jobs")
        return {t: await self.count(t) for t in tables}

    async def set_mode(self, mode: str) -> None:
        async with self.sf() as s:
            await s.execute(
                text("UPDATE accounts SET x_read_mode = :m WHERE id = :id"),
                {"m": mode, "id": self.account_id},
            )
            await s.commit()


async def make_stand(
    sf: SessionFactory,
    clock: FakeClock,
    *,
    notifier: NotifierLike | None = None,
    embedder: FakeEmbedder | None = None,
    handle: str = "me",
) -> Stand:
    async with UnitOfWork(sf) as uow:
        user, _ = await UsersRepo(uow.session).get_or_create(OWNER_TG, is_admin=True)
        accounts = AccountsRepo(uow.session)
        acc = await accounts.get_by_handle(handle)
        if acc is None:
            acc = await accounts.create(handle=handle, niche="Крипта", lang="ru")
            await accounts.add_member(acc.id, user.id, role="owner")
        account_id, user_id = acc.id, user.id
    x = FakeX()
    llm = AutoTagLLM(name="deepseek", clock=clock)
    emb = embedder or FakeEmbedder()
    blobs = MemoryBlobStore()
    note = notifier or RecordingNotifier()

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(sf)

    async def x_for(_: UUID) -> XClient:
        return x

    service = IngestService(
        uow_factory=uow_factory,
        clock=clock,
        tagger=Tagger(build_gateway(sf, clock, llm)),
        embedder=emb,
        x_for=x_for,
        notifier=note,
        blobs=blobs,
    )
    return Stand(
        sf=sf,
        clock=clock,
        account_id=account_id,
        user_id=user_id,
        x=x,
        llm=llm,
        embedder=emb,
        blobs=blobs,
        notifier=note,
        service=service,
        queue=JobQueue(uow_factory, clock),
    )
