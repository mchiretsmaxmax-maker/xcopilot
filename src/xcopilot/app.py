"""Сборка живого приложения: один процесс — бот + воркер + планировщик (ТЗ P25, ADR 0022).

`build_app(settings)` собирает зависимости из настроек (движок БД, шлюз LLM, X по аккаунту,
ингест, стиль, «Мозг», писатель, бот) и реестр задач. `App.startup()` выполняет то, что
этапы просили сделать при старте процесса: подписчики событий, промпты в БД, партиции
журналов, сид анти-AI правил. `serve_app` запускает три цикла параллельно и останавливает
их по `stop`; падение одного цикла останавливает остальные (процесс перезапустит compose).
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from aiogram import Bot

from xcopilot.analytics.kit import build_analytics
from xcopilot.bot import budget_events
from xcopilot.bot.app import run_polling
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.failures import DeadJobReporter
from xcopilot.bot.notify import Notifier
from xcopilot.bot.progress import ProgressBoard
from xcopilot.bot.routers.learn import build_ingest_debouncer
from xcopilot.brain import seed
from xcopilot.brain.analyze import Analyzer
from xcopilot.brain.history import HistoryImporter
from xcopilot.brain.jobs import BrainDeps, install_analysis_on_ingest
from xcopilot.brain.provider import make_knowledge
from xcopilot.clock import Clock, SystemClock
from xcopilot.config import Settings
from xcopilot.db.engine import SessionFactory, create_engine, session_factory
from xcopilot.db.partitions import ensure_all_partitions
from xcopilot.db.repos import AccountsRepo, SecretsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.embeddings.fastembed_impl import FastEmbedder
from xcopilot.errors import NotFound
from xcopilot.ingest.factory import build_blob_store
from xcopilot.ingest.parsers.screenshot import GatewayScreenshotReader
from xcopilot.ingest.screen import Screener
from xcopilot.ingest.service import IngestService
from xcopilot.ingest.tagger import Tagger
from xcopilot.jobs.handlers import JobDeps, build_registry
from xcopilot.jobs.queue import JobQueue
from xcopilot.jobs.scheduler import Scheduler
from xcopilot.jobs.worker import Registry, Worker, describe_error
from xcopilot.llm.factory import build_budget_guard, build_gateway
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.ops.alerts import AlertDeps
from xcopilot.quality.brain_detector import BrainRubricDetector
from xcopilot.quality.loop import GatewayRewriter, QualityGate
from xcopilot.review.kit import build_review
from xcopilot.review.translate import Translator
from xcopilot.security.crypto import SecretBox
from xcopilot.style.extractor import StyleExtractor
from xcopilot.style.service import StyleService
from xcopilot.writer.drafts import WriterService
from xcopilot.writer.topics import TopicService
from xcopilot.x.api_v2 import XApiV2
from xcopilot.x.auth import SECRET_TOKENS, XTokens
from xcopilot.x.client import XClient, XMode, XService
from xcopilot.x.cost import DailyDedup
from xcopilot.x.oembed import OEmbedReader

log = structlog.get_logger()

HTTP_TIMEOUT = 20.0
Poller = Callable[[BotDeps, asyncio.Event], Awaitable[None]]


class ServiceFailed(RuntimeError):
    """Один из циклов (бот, воркер, планировщик) остановился с ошибкой — процесс выходит."""


def install_hooks() -> None:
    """Подписчики emit, которые этапы просили подключить при сборке (идемпотентно)."""
    install_analysis_on_ingest()  # 7A: source.ingested → brain.analyze
    budget_events.install_budget_notifications()  # 5: budget.* → bot.notify_budget


@dataclass
class App:
    settings: Settings
    session_factory: SessionFactory
    uow_factory: Callable[[], UnitOfWork]
    queue: JobQueue
    bot_deps: BotDeps
    registry: Registry
    x_for: Callable[[UUID], Awaitable[XClient]]
    clock: Clock
    worker: Worker
    scheduler: Scheduler
    http: httpx.AsyncClient
    dispose: Callable[[], Awaitable[None]]
    # этап 15: сервисы для eval (tests/evals) и /health
    gateway: LLMGateway | None = None
    writer: WriterService | None = None
    gate: QualityGate | None = None
    embedder: Embedder | None = None

    def model_names(self) -> dict[str, str]:
        """Роль → модель (для baseline.json)."""
        return self.gateway.model_names() if self.gateway is not None else {}

    async def startup(self) -> None:
        """Подписчики, промпты, партиции, сид «Мозга» — до запуска циклов."""
        install_hooks()
        async with self.uow_factory() as uow:
            synced = await PromptRegistry().sync(uow.session)
            created = await ensure_all_partitions(uow.session, today=self.clock.now().date())
            seeded = await seed.load_global_seed(uow.session)
        log.info(
            "app_startup",
            prompts=len(synced),
            partitions=sum(len(v) for v in created.values()),
            seed_items=seeded,
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.bot_deps.bot.session.close()
        await self.dispose()


def _secret_box(settings: Settings) -> SecretBox | None:
    if not settings.secrets_key:
        return None
    try:
        return SecretBox.from_string(settings.secrets_key)
    except ValueError:
        log.warning("secrets_key_invalid")
        return None


def build_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    bot: Bot | None = None,
    worker_poll: float = 1.0,
    scheduler_interval: float = 60.0,
    gateway: LLMGateway | None = None,
    embedder: Embedder | None = None,
    x_client: Callable[[UUID], Awaitable[XClient]] | None = None,
) -> App:
    """Собрать приложение из настроек; сеть и БД не трогаются до startup / serve.

    `gateway`, `embedder`, `x_client` — подмена моделей, эмбеддера и X (сквозные тесты этапа 15);
    в проде не передаются.
    """
    clock = clock or SystemClock()
    engine = create_engine(settings)
    sf = session_factory(engine)

    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    queue = JobQueue(uow, clock)
    gateway = gateway or build_gateway(settings, uow, clock)
    budget = build_budget_guard(settings, uow, clock)
    bot = bot or Bot(settings.bot_token)
    notifier = Notifier(bot, uow)
    http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    oembed = OEmbedReader(http)
    dedup = DailyDedup()  # один на процесс (этап 6)
    box = _secret_box(settings)
    tokens = XTokens(http, uow, box, clock, notifier) if box is not None else None

    async def x_for(account_id: UUID) -> XClient:
        """XService аккаунта в его режиме; API — только если X подключён (есть токены)."""
        async with uow() as u:
            acc = await AccountsRepo(u.session).get(account_id)
            if acc is None:
                raise NotFound(f"аккаунт {account_id} не найден")
            connected = await SecretsRepo(u.session, account_id).get_by_kind(SECRET_TOKENS)
        api = None
        if tokens is not None and connected is not None:
            api = XApiV2(
                http,
                lambda: tokens.get_access_token(account_id),
                clock=clock,
                sleep=asyncio.sleep,
            )
        mode: XMode = acc.x_read_mode  # type: ignore[assignment]  # CHECK в схеме
        return XService(
            account_id=account_id,
            mode=mode,
            oembed=oembed,
            api=api,
            budget=budget,
            uow_factory=uow,
            clock=clock,
            own_handle=acc.handle,
            own_user_id=acc.x_user_id,
            dedup=dedup,
            jobs=queue,
        )

    if x_client is not None:
        x_for = x_client  # type: ignore[assignment]  # подмена X целиком (тесты)

    embedder = embedder or FastEmbedder()  # модель грузится при первом вызове
    ingest = IngestService(
        uow_factory=uow,
        clock=clock,
        tagger=Tagger(gateway),
        embedder=embedder,
        x_for=x_for,
        notifier=notifier,
        blobs=build_blob_store(settings),
        screenshots=GatewayScreenshotReader(gateway),
        screener=Screener(gateway),
    )
    style = StyleService(uow, StyleExtractor(uow, gateway))
    analyzer = Analyzer(uow, gateway, clock, notifier=notifier)
    importer = HistoryImporter(uow, x_for, budget, clock)
    knowledge = make_knowledge(embedder=embedder, clock=clock)
    writer = WriterService(
        uow_factory=uow,
        gateway=gateway,
        embedder=embedder,
        clock=clock,
        knowledge=knowledge,
        probation=knowledge.probation,
        pack_recorder=knowledge,
    )
    topics = TopicService(
        uow_factory=uow, gateway=gateway, embedder=embedder, clock=clock, knowledge=knowledge
    )
    gate = QualityGate(
        uow_factory=uow,
        judge=gateway,
        rewriter=GatewayRewriter(gateway),
        clock=clock,
        detector=BrainRubricDetector(gateway, clock),
    )
    review = build_review(  # этап 12: карточки, решения, публикация
        bot=bot,
        uow_factory=uow,
        clock=clock,
        queue=queue,
        x_for=x_for,
        relinter=gate,
        translator=Translator(uow, gateway),  # русский перевод в карточке (cheap)
    )
    bot_deps = BotDeps(
        bot=bot, settings=settings, uow_factory=uow, clock=clock, queue=queue, ingest=ingest
    )
    bot_deps.review = review
    bot_deps.knowledge = knowledge
    bot_deps.style = style
    bot_deps.ingest_batch = build_ingest_debouncer(bot_deps)
    brain_deps = BrainDeps(uow_factory=uow, clock=clock, llm=gateway, embedder=embedder)
    bot_deps.brain = brain_deps  # 9C: решения владельца в боте
    analytics = build_analytics(  # этап 14: синк своих постов, отчёты, аналитик
        uow_factory=uow,
        clock=clock,
        x_for=x_for,
        queue=queue,
        matcher=review.matcher,
        brain=brain_deps,
        notifier=notifier,
        gateway=gateway,
    )
    bot_deps.analytics = analytics
    bot_deps.open_since = gateway.open_since  # этап 15: /health
    alerts = AlertDeps(
        uow_factory=uow,
        clock=clock,
        budget=budget,
        open_since=gateway.open_since,
        env=settings.env,
    )

    async def tz_for(account_id: UUID) -> ZoneInfo:
        async with uow() as u:
            acc = await AccountsRepo(u.session).get(account_id)
        return ZoneInfo(acc.tz) if acc is not None else ZoneInfo("UTC")

    board = ProgressBoard(notifier, clock, tz_for)
    bot_deps.progress = board
    registry = build_registry(
        JobDeps(
            uow_factory=uow,
            clock=clock,
            queue=queue,
            ingest=ingest,
            analyzer=analyzer,
            importer=importer,
            writer=writer,
            topics=topics,
            brain=brain_deps,
            x_for=x_for,
            notifier=notifier,
            bot=bot_deps,
            quality=gate,
            review=review,
            analytics=analytics,
            alerts=alerts,
        )
    )
    worker = Worker(
        queue,
        registry,
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        poll_interval=worker_poll,
        on_dead=DeadJobReporter(notifier, board),
    )
    return App(
        settings=settings,
        session_factory=sf,
        uow_factory=uow,
        queue=queue,
        bot_deps=bot_deps,
        registry=registry,
        x_for=x_for,
        clock=clock,
        worker=worker,
        scheduler=Scheduler(uow, clock, interval=scheduler_interval),
        http=http,
        dispose=engine.dispose,
        gateway=gateway,
        writer=writer,
        gate=gate,
        embedder=embedder,
    )


async def serve_app(app: App, stop: asyncio.Event, *, poll: Poller = run_polling) -> None:
    """Старт, затем бот (long polling), воркер и планировщик до `stop`; потом закрыть всё."""

    failed: list[str] = []

    async def guarded(name: str, coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception as exc:
            log.error("component_crashed", component=name, error=describe_error(exc))
            failed.append(name)
        else:
            if not stop.is_set():  # цикл не должен заканчиваться сам
                log.error("component_exited", component=name)
                failed.append(name)
        stop.set()  # остальные циклы завершаются штатно

    try:
        await app.startup()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(guarded("bot", poll(app.bot_deps, stop)))
            tg.create_task(guarded("worker", app.worker.run(stop)))
            tg.create_task(guarded("scheduler", app.scheduler.run(stop)))
    finally:
        await app.aclose()
        log.info("app_stopped")
    if failed:
        raise ServiceFailed(", ".join(failed))
