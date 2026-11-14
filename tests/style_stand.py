"""Стенд паспорта стиля: собственные посты в БД, шлюз с подделкой писателя, ответы модели."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeJudge, FakeLLM
from xcopilot.db.repos import AccountsRepo, ChunksRepo, SourcesRepo
from xcopilot.db.repos.sources import material_kind_stance
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PACKAGED_DIR, PromptRegistry
from xcopilot.llm.resilience import RetryPolicy
from xcopilot.style.extractor import StyleExtractor
from xcopilot.style.service import StyleService

SessionFactory = async_sessionmaker[AsyncSession]
LEGACY_MODELS = Path(__file__).parent / "fixtures" / "models_anthropic_jev.toml"

LOWER_POSTS = [
    "блобы сделали l2 дешевле в десять раз",
    "комиссии упали, а пользователи остались",
    "ждём апгрейд сети, без паники",
    "роллапы съедают рынок тихо",
    "газ в сети дешевеет, это хороший знак",
    "не гонись за хайпом, читай документацию",
]


async def new_account(sf: SessionFactory, handle: str = "style_acc") -> UUID:
    async with UnitOfWork(sf) as uow:
        return (await AccountsRepo(uow.session).create(handle=handle, tz="UTC")).id


async def seed_chunks(
    sf: SessionFactory,
    account_id: UUID,
    texts: Sequence[str],
    *,
    kind: str = "own_post",
    fmt: str = "short",
    status: str = "indexed",
) -> list[UUID]:
    """По одному источнику, документу и куску на каждый текст; вернуть id кусков."""
    ids: list[UUID] = []
    async with UnitOfWork(sf) as uow:
        sources = SourcesRepo(uow.session, account_id)
        chunks = ChunksRepo(uow.session, account_id)
        for text in texts:
            digest = hashlib.sha256(f"{kind}:{text}".encode()).hexdigest()
            src_kind, stance = material_kind_stance(kind)  # ТЗ v3.1: материал → kind + stance
            source, _ = await sources.add_or_get(
                kind=src_kind,
                stance=stance,
                origin="manual_text",
                content_hash=digest,
                status=status,
            )
            doc = await sources.add_document(source.id, text=text, text_clean=text)
            chunk = await chunks.add_chunk(
                doc.id, idx=0, text=text, tags=[f"material:{kind}", f"format:{fmt}"], lang="ru"
            )
            ids.append(chunk.id)
    return ids


def build_gateway(sf: SessionFactory, clock: FakeClock, writer: FakeLLM) -> LLMGateway:
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    async def no_sleep(_: float) -> None:
        return None

    return LLMGateway(
        models=ModelsConfig.load(LEGACY_MODELS),
        pricing=PricingTable.load(),
        providers={
            "anthropic": writer,
            "deepseek": FakeLLM(name="deepseek"),
            "jev": FakeJudge(name="jev"),
        },
        prompts=PromptRegistry(PACKAGED_DIR),
        budget=BudgetGuard(uow, clock, default_day_limits={"llm": 50_000_000, "x_api": 1_000_000}),
        uow_factory=uow,
        clock=clock,
        retry=RetryPolicy(),
        sleep=no_sleep,
    )


def model_reply(**fields: Any) -> str:
    """JSON-ответ модели паспорта (`ExtractOut`) с переопределёнными полями."""
    body: dict[str, Any] = {"summary": "спокойный автор", "tone": "спокойный, точный"}
    body.update(fields)
    return json.dumps(body, ensure_ascii=False)


def make_services(
    sf: SessionFactory, clock: FakeClock, writer: FakeLLM, *, budget_tokens: int = 6000
) -> tuple[StyleService, StyleExtractor]:
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    extractor = StyleExtractor(uow, build_gateway(sf, clock, writer), budget_tokens=budget_tokens)
    return StyleService(uow, extractor), extractor
