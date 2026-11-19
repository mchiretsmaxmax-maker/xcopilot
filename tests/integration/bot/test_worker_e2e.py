"""E2E через настоящий воркер: пост боту → ingest.process → brain.analyze → карточка разбора."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import analysis_json
from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.llm import FakeLLM
from tests.ingest_stand import build_gateway, make_stand
from tests.integration.bot.conftest import OWNER, Client, count
from xcopilot.app import install_hooks
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.brain.analyze import Analyzer
from xcopilot.brain.jobs import BrainDeps
from xcopilot.jobs.handlers import JobDeps, build_registry
from xcopilot.jobs.worker import Worker
from xcopilot.metrics.events import clear_listeners

SF = async_sessionmaker[AsyncSession]
POST = "I lost $40k on one trade and learned to size every position since."


@pytest.fixture
def hooks() -> Iterator[None]:
    install_hooks()
    yield
    clear_listeners()


async def test_owner_post_flows_to_analysis_card_through_worker(
    hooks: None,
    session_factory: SF,
    clock: FakeClock,
    telegram: FakeTelegram,
    deps: BotDeps,
) -> None:
    notifier = Notifier(telegram.bot, deps.uow_factory)
    stand = await make_stand(session_factory, clock, notifier=notifier, handle="acc")
    deps.ingest = stand.service
    cheap = FakeLLM(name="deepseek", clock=clock).push(analysis_json(POST))
    analyzer = Analyzer(
        deps.uow_factory, build_gateway(session_factory, clock, cheap), clock, notifier=notifier
    )
    unused = cast(Any, object())

    async def x_for(_: object) -> Any:
        return stand.x

    job_deps = JobDeps(
        uow_factory=deps.uow_factory,
        clock=clock,
        queue=deps.queue,
        ingest=stand.service,
        analyzer=analyzer,
        importer=unused,
        writer=unused,
        topics=unused,
        brain=BrainDeps(uow_factory=deps.uow_factory, clock=clock),
        x_for=x_for,
        notifier=notifier,
        bot=deps,
    )
    worker = Worker(deps.queue, build_registry(job_deps), worker_id="e2e")
    owner = Client(build_dispatcher(deps), telegram, OWNER)

    replies = await owner.say(POST)
    assert replies[-1].text == "Принял 1 пост. Изучаю…"
    processed = await worker.drain()

    # ingest.process, brain.analyze (поставлен подписчиком), brain.detect (после разбора, этап 15),
    # brain.extract (после детекта; пачечное правило решает, есть ли работа)
    assert processed == 4
    assert await count(session_factory, "jobs", "status = 'done'") == 4
    # свой пост → проверка автопересборки паспорта ждёт паузу (feat/clean-voice), не выполнена
    assert await count(session_factory, "jobs", "status <> 'done'") == 1
    assert (
        await count(session_factory, "jobs", "kind = 'style.auto_rebuild' AND status = 'queued'")
        == 1
    )
    assert await count(session_factory, "material_analyses") == 1
    cards = [t for t in telegram.texts_to(OWNER) if t.startswith("🧩 Разбор")]
    assert len(cards) == 1
    assert "Хук:" in cards[0]
