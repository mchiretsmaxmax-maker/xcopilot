"""Стенд сквозных тестов этапа 15: собранное приложение (`build_app`) на подделках.

Настоящие: PostgreSQL, диспетчер бота со всеми роутерами и middleware, воркер, очередь,
сервисы всех этапов, шлюз моделей (`LLMGateway`: шаблоны промптов, проверка схем, журнал
`llm_calls`, бюджет, предохранители).
Подделки: провайдеры моделей (ответ по ключу промпта — `RoutedLLM`, судья по именам вопросов —
`RoutedJudge`), X (`FakeX`), Telegram (`FakeTelegram`), эмбеддер, часы.

Короткий путь один: доказательства по чужим материалам (исходы по метрикам авторов — этапы 7A
и 9A, там свои тесты) стенд кладёт в `knowledge_evidence` SQL-ом, чтобы знание набрало порог
автоиспытания. Переход в 🧪 при этом делает настоящий ночной проход `brain.nightly`.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text

from tests.brain_core_stand import candidate_judge
from tests.brain_stand import analysis_json
from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.llm import DEFAULT_USAGE
from tests.fakes.telegram import SentMessage
from tests.fakes.x import FakeX
from tests.integration.bot.conftest import OWNER, Client
from tests.integration.brain.extract_helpers import candidate, result
from tests.integration.db.brain_helpers import evidence
from tests.quality_stand import judge_answers
from tests.style_stand import model_reply
from tests.writer_stand import LEGACY_MODELS, draft_reply, topics_reply
from xcopilot.app import App, build_app
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.routers.learn import build_ingest_debouncer
from xcopilot.brain import api
from xcopilot.brain import owner as owner_api
from xcopilot.brain.schemas import TaskSpec
from xcopilot.config import Settings
from xcopilot.db.engine import create_engine, session_factory
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.providers.base import DecisionResult, ProviderResult, TextRequest
from xcopilot.llm.resilience import RetryPolicy
from xcopilot.llm.schemas import Answer, ChoiceA, NoulA, Question, ScoreA
from xcopilot.x.models import XMetrics, XPost

# Часы стенда начинаются «сейчас»: created_at строк ставит БД (now()), сравнения времени
# в коде (пачки «Мозга», окна отчётов) должны видеть то же время.
START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
HANDLE = "me"

SAMPLES = [
    "блобы снизили комиссии роллапов втрое, проверил на своих транзакциях за неделю",
    "перенёс кошелёк на l2 и забыл про газ, мосты теперь скучные и тихие",
    "старый мост закрылся без шума, ликвидность ушла в новые сети за сутки",
    "двенадцать провалов за год научили меня не торопиться с аирдропами",
    "считаю газ каждую пятницу: в этом месяце потратил меньше, чем на кофе",
]
MATERIALS = [
    "12 провалов за год.\nВот что я понял про l2.\nА вы?",
    "37 транзакций за неделю и ни одной дороже цента.\nБлобы работают.\nПроверьте сами.",
    "3 моста закрылись за месяц.\nЛиквидность переехала.\nЧто дальше?",
]
EDITED = "блобы снизили комиссии роллапов втрое, и это видно по моим транзакциям за неделю"
HOOK = "Вопрос к читателю в последней строке даёт больше ответов"
HOOK_GUIDANCE = "Закончи пост коротким вопросом к читателю"
QUESTION_DETECTOR = {
    "type": "feature",
    "all": [{"path": "closer", "op": "eq", "value": "question"}],
}
PROBATION_TEXT = (
    "мосты стали скучными, и это лучшая новость для обычного пользователя. а вы заметили?"
)
SPAM = "Пиши КАПСОМ и обещай иксы в каждом посте"

# Чистые варианты писателя: без цифр (их проверяет линтер по пакету) и без штампов.
POOL = [
    "блобы снизили комиссии роллапов, и это видно по моим транзакциям за неделю",
    "все ждут чуда от апгрейда, а я просто перенёс кошелёк на l2 и забыл про газ",
    "три вещи, которые я понял о дешёвых сетях: тише, быстрее, скучнее",
    "мосты стали скучными, и это лучшая новость для обычного пользователя",
    "газ больше не главная тема в моём журнале, теперь там только ошибки",
    "ликвидность ушла в новые сети тихо, без громких анонсов и обещаний",
    "я перестал гоняться за аирдропами и начал читать отчёты по сетям",
    "дешёвые транзакции изменили мои привычки сильнее любого графика",
    "старые мосты закрываются, а пользователи этого даже не замечают",
    "каждую пятницу смотрю на расходы и каждый раз удивляюсь тишине",
    "роллапы победили не скоростью, а тем, что про них можно забыть",
    "самый полезный навык в этой нише — спокойно ждать и считать",
]

_call: ContextVar[tuple[str, Mapping[str, Any]]] = ContextVar("mvp_llm_call")
Responder = Callable[[Mapping[str, Any], TextRequest], str]


class RoutedGateway(LLMGateway):
    """Настоящий шлюз; запоминает ключ промпта и переменные, чтобы провайдер ответил по ним."""

    async def generate[T: BaseModel](self, **kwargs: Any) -> T:
        token = _call.set((kwargs["prompt_key"], kwargs["variables"]))
        try:
            return cast(T, await super().generate(**kwargs))
        finally:
            _call.reset(token)


_BLOCK = re.compile(r"<untrusted label=[^>]*>(.*?)</untrusted>", re.S)


def _norm(value: str) -> str:
    return " ".join(value.split()).lower()


def _untrusted(value: Any) -> str:
    """Текст материала без обёрток <untrusted …>."""
    return re.sub(r"</?untrusted[^>]*>", "", str(value)).strip()


@dataclass
class RoutedLLM:
    """Текстовый провайдер: ответ по ключу промпта; ключ без ответа — ошибка теста."""

    name: str
    routes: dict[str, Responder] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        key, variables = _call.get(("?", {}))
        self.calls.append(key)
        responder = self.routes.get(key)
        if responder is None:
            raise AssertionError(f"{self.name}: нет ответа на промпт {key}")
        return ProviderResult(text=responder(variables, request), usage=DEFAULT_USAGE, model=model)


@dataclass
class RoutedJudge:
    """Судья: ответы по именам вопросов (ворота 11, экран ингеста, извлечение 9A)."""

    name: str = "jev"
    overrides: dict[str, Answer] = field(default_factory=dict)
    calls: int = 0

    async def decide(
        self, *, model: str, state: str, questions: Mapping[str, Question]
    ) -> DecisionResult:
        self.calls += 1
        quality = judge_answers()
        screen: dict[str, Answer] = {
            "instructions_for_ai": NoulA(p=0.02),
            "is_content": NoulA(p=0.98),
            "risky": NoulA(p=0.02),
        }
        brain = candidate_judge()(state, questions)
        answers: dict[str, Answer] = {}
        for name, q in questions.items():
            if name in self.overrides:
                answers[name] = self.overrides[name]
            elif name in quality:
                answers[name] = quality[name]
            elif name in screen:
                answers[name] = screen[name]
            elif q.kind == "choice" and name not in ("layer", "relation"):
                first = next(iter(q.options))
                answers[name] = ChoiceA(choice=first, confidence=0.9, probabilities={first: 0.9})
            elif q.kind == "score" and name != "natural":
                top = len(q.levels) - 1
                answers[name] = ScoreA(score=top, confidence=0.9, probabilities={top: 0.9})
            else:
                answers[name] = brain[name]
        return DecisionResult(answers=answers, usage=DEFAULT_USAGE, model=model)


@dataclass
class Brain:
    """Что вернёт модель извлечения знаний в следующий раз."""

    statement: str = HOOK


def _routes(brain: Brain, pool: Iterator[str]) -> dict[str, Responder]:
    own = {_norm(t) for t in SAMPLES}

    def tag(_: Mapping[str, Any], request: TextRequest) -> str:
        blocks = [b for m in request.messages for b in _BLOCK.findall(m.content)] or [""]
        items = [
            {
                "material": "own_post" if _norm(b) in own else "liked_sample",
                "topics": ["L2"],
                "format": "short",
                "lang": "ru",
            }
            for b in blocks
        ]
        return json.dumps({"items": items})

    def drafts(v: Mapping[str, Any], __: TextRequest) -> str:
        if "### Испытание" in str(v.get("knowledge", "")):
            return draft_reply(PROBATION_TEXT)  # вариант 🧪 применяет испытуемый приём
        return draft_reply(next(pool), next(pool), next(pool))

    def analyze(v: Mapping[str, Any], _: TextRequest) -> str:
        material = _untrusted(v.get("material", ""))
        closer = "question" if material.rstrip().endswith("?") else "takeaway"
        return analysis_json(material, lang="ru", closer=closer)

    def extract(v: Mapping[str, Any], _: TextRequest) -> str:
        found = set(re.findall(r"\bM\d+\b", str(v.get("materials", ""))))
        labels = sorted(found, key=lambda s: int(s[1:]))
        hook = brain.statement == HOOK
        cand = candidate(
            kind="thesis" if hook else "hook_pattern",
            element="closer" if hook else "hook",
            statement=brain.statement,
            guidance=HOOK_GUIDANCE if hook else f"Сделай так: {brain.statement}",
            supporting=labels[:6] or ["M1"],
            **({"detector": QUESTION_DETECTOR} if hook else {}),
        )
        return result(cand).model_dump_json()

    def daily(_: Mapping[str, Any], __: TextRequest) -> str:
        return json.dumps({"text": "Вчера: пост вышел, подробности в /stats."}, ensure_ascii=False)

    def empty(body: dict[str, Any]) -> Responder:
        return lambda _, __: json.dumps(body)

    return {
        "ingest.tag": tag,
        "style.extract.v1": lambda _, __: model_reply(),
        "writer.topics.v1": lambda _, __: topics_reply(
            "почему комиссии в l2 падают", "мосты стали скучными", "газ по пятницам"
        ),
        "writer.draft.v1": drafts,
        "writer.rewrite.v1": lambda _, __: json.dumps({"text": next(pool)}, ensure_ascii=False),
        "brain.analyze_material.v1": analyze,
        "brain.extract_knowledge.v1": extract,
        "reports.daily.v1": daily,
        "analyst.report.v1": empty({"worked": [], "improve": []}),
        "teacher.from_reviews.v1": empty({"candidates": []}),
    }


@dataclass
class MvpStand:
    app: App
    telegram: FakeTelegram
    clock: FakeClock
    x: FakeX
    owner: Client
    writer: RoutedLLM
    cheap: RoutedLLM
    judge: RoutedJudge
    brain: Brain
    _account: UUID | None = None

    # ── общее ───────────────────────────────────────────────────────────────────────────

    @property
    def account_id(self) -> UUID:
        assert self._account is not None, "сначала onboard()"
        return self._account

    @property
    def queue(self) -> JobQueue:
        return self.app.queue

    async def drain(self) -> int:
        """Прогнать очередь до пустой (задачи с паузой повтора не ждём)."""
        if self.app.bot_deps.ingest_batch is not None:
            await self.app.bot_deps.ingest_batch.drain()
        return await self.app.drain(500)  # обе полосы воркера (аудит E)

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.app.session_factory() as s:
            return list((await s.execute(text(sql), params)).all())

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        rows = await self.rows(f"SELECT count(*) FROM {table} WHERE {where}", **params)
        return int(rows[0][0])

    async def run_job(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        await self.queue.enqueue(kind, payload or {}, account_id=self.account_id)
        await self.drain()

    # ── шаги владельца ──────────────────────────────────────────────────────────────────

    async def onboard(self, samples: list[str]) -> None:
        o = self.owner
        await o.say("/start")
        await o.press("Крипта")
        await o.press("Русский")
        await o.say(f"@{HANDLE}")  # свой хэндл в X: им подписаны свои посты и ссылки
        for sample in samples:
            await o.say(sample)
        await o.press("Дальше")
        await o.press("Пропустить")
        await o.press("Всё верно")
        rows = await self.rows(
            "SELECT a.id FROM accounts a JOIN account_members m ON m.account_id = a.id"
            " JOIN users u ON u.id = m.user_id WHERE u.tg_user_id = :t",
            t=OWNER,
        )
        assert rows, "онбординг не создал аккаунт"
        self._account = rows[0][0]
        await o.press_data("obr:skip")
        await self.drain()

    async def send_material(self, body: str, *, author: str | None = None) -> None:
        await self.owner.say(body)
        await self.drain()

    async def build_passport(self) -> None:
        o = self.owner
        await o.press_data("menu:style")
        await o.press("🔁 Пересобрать")
        await o.press("✅ Применить")

    async def request_topics(self) -> None:
        await self.owner.press_data("menu:topics")
        await self.drain()

    async def write_first_topic(self) -> None:
        await self.owner.press("Писать")
        await self.drain()

    async def write_topic(self, topic: str) -> None:
        o = self.owner
        await o.press_data("menu:write")
        await o.press("Пост")
        await o.say(topic)
        await self.drain()

    def cards(self) -> list[SentMessage]:
        return [m for m in self.telegram.sent if m.text.startswith("📝 Вариант")]

    def own_post(self, post_id: str, body: str, *, likes: int = 0, views: int = 0) -> None:
        post = XPost(
            id=post_id,
            text=body,
            author_handle=HANDLE,
            created_at=self.clock.now() - timedelta(minutes=5),
            source="api",
            metrics=XMetrics(
                likes=likes, reposts=2, replies=3, quotes=0, bookmarks=4, impressions=views
            ),
        )
        self.x.add_post(post, own=True)

    # ── «Мозг» ──────────────────────────────────────────────────────────────────────────

    async def extract_knowledge(
        self, statement: str, *, materials: list[str] | None = None
    ) -> UUID:
        """Материалы (если даны) → разбор → `brain.extract` → знание; плюс доказательства."""
        self.brain.statement = statement
        # после detect извлечение ставится само; здесь его гоним вручную, когда пачка собрана
        registry: dict[str, HandlerSpec] = self.app.registry  # type: ignore[assignment]
        real = registry["brain.extract"]

        async def _skip(job: Job) -> object:
            return None

        registry["brain.extract"] = HandlerSpec(_skip)
        try:
            for body in materials or []:
                await self.send_material(body)
        finally:
            registry["brain.extract"] = real
        self.clock.advance(25 * 3600)  # извлечение — ночной проход: свежие пачки ждут сутки
        await self.run_job("brain.extract")
        rows = await self.rows(
            "SELECT id FROM knowledge_items WHERE account_id = :a AND statement = :s",
            a=self.account_id,
            s=statement,
        )
        assert rows, f"знание «{statement}» не создано"
        item = cast(UUID, rows[0][0])
        await self._seed_evidence(item)
        return item

    async def _seed_evidence(self, item: UUID) -> None:
        """Подтверждения от трёх независимых авторов (короткий путь, см. докстринг модуля)."""
        async with self.app.session_factory() as s:
            for _ in range(3):
                author = (
                    await s.execute(
                        text(
                            "INSERT INTO authors (account_id, handle) VALUES (:a, :h) RETURNING id"
                        ),
                        {"a": self.account_id, "h": f"ev_{uuid4().hex[:8]}"},
                    )
                ).scalar_one()
                src = (
                    await s.execute(
                        text(
                            "INSERT INTO sources (account_id, kind, stance, origin, content_hash,"
                            " author_id, status) VALUES (:a, 'post', 'inspiration', 'manual_text',"
                            " :h, :au, 'indexed') RETURNING id"
                        ),
                        {"a": self.account_id, "h": uuid4().hex, "au": author},
                    )
                ).scalar_one()
                await evidence(
                    s, self.account_id, item, observed_at=self.clock.now(), source_id=src,
                    author_id=author, outcome_tier="hit", w_author=1.0,
                )  # fmt: skip
            await s.commit()

    async def confirm_item(self, item_id: UUID) -> None:
        """Владелец подтверждает знание (как кнопка «Подтвердить» в /brain, этап 9C)."""
        brain = self.app.bot_deps.brain
        assert brain is not None
        rows = await self.rows("SELECT id FROM users WHERE tg_user_id = :t", t=OWNER)
        async with self.app.uow_factory() as uow:
            await owner_api.confirm(brain.ctx(uow.session, self.account_id), item_id, rows[0][0])

    async def run_nightly(self) -> None:
        await self.run_job("brain.nightly")

    async def item_status(self, item_id: UUID) -> str:
        rows = await self.rows("SELECT status FROM knowledge_items WHERE id = :i", i=item_id)
        return str(rows[0][0])

    async def item_weights(self, item_id: UUID) -> dict[str, float]:
        rows = await self.rows(
            "SELECT eff_alpha, quality FROM knowledge_items WHERE id = :i", i=item_id
        )
        return {"eff_alpha": float(rows[0][0]), "quality": float(rows[0][1])}

    async def probation_draft(self, item_id: UUID) -> UUID:
        rows = await self.rows(
            "SELECT ka.draft_id FROM knowledge_applications ka WHERE ka.item_id = :i"
            " AND ka.slot = 'probation' ORDER BY ka.draft_id DESC LIMIT 1",
            i=item_id,
        )
        assert rows, "нет варианта 🧪"
        return cast(UUID, rows[0][0])

    async def show_variant(self, draft_id: UUID) -> SentMessage:
        """Листать карточку ▶ (бот редактирует одно сообщение), пока не покажется этот вариант."""
        body = await self.draft_text(draft_id)
        for _ in range(5):
            # статус-сообщения прогресса (fix/bot-ux-progress) — не карточки
            edits = [e for e in self.telegram.edits() if (e.text or "").startswith("📝")]
            if edits:
                card = SentMessage(OWNER, edits[-1].text or "", edits[-1].reply_markup)
            else:
                card = self.cards()[-1]
            if body in card.text:
                return card
            await self.press_on(card, "▶")
        raise AssertionError("вариант не найден в карточке")

    async def press_on(self, card: SentMessage, label: str) -> list[SentMessage]:
        """Нажать кнопку именно на этой (возможно отредактированной) карточке."""
        return await self.owner.press_data(card.callbacks[card.buttons.index(label)])

    async def draft_text(self, draft_id: UUID) -> str:
        rows = await self.rows("SELECT text FROM drafts WHERE id = :d", d=draft_id)
        return str(rows[0][0])

    async def pack_items(self, topic: str) -> list[UUID]:
        task = TaskSpec(format="post", lang="ru", topic=topic)
        async with self.app.session_factory() as s:
            pack = await api.build_pack(
                s, self.account_id, task, embedder=FakeEmbedder(), clock=self.clock, record=False
            )
        return [i.item_id for i in pack.items]

    async def rollback_from_bot(self, *, days_back: int) -> None:
        o = self.owner
        labels = {1: "Вчера", 7: "7 дней назад", 30: "30 дней назад"}
        await o.say("/brain undo")
        await o.press(labels[days_back])
        await o.press("Откатить")


def _gateway(
    sf: Any, clock: FakeClock, writer: RoutedLLM, cheap: RoutedLLM, judge: RoutedJudge
) -> RoutedGateway:
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    async def no_sleep(_: float) -> None:
        return None

    return RoutedGateway(
        models=ModelsConfig.load(LEGACY_MODELS),
        pricing=PricingTable.load(),
        providers={"anthropic": writer, "deepseek": cheap, "jev": judge},
        prompts=PromptRegistry(),
        budget=BudgetGuard(uow, clock, default_day_limits={"llm": 50_000_000, "x_api": 5_000_000}),
        uow_factory=uow,
        clock=clock,
        retry=RetryPolicy(),
        sleep=no_sleep,
    )


async def make_mvp_stand(db_url: str, telegram: FakeTelegram, tmp_path: Path) -> MvpStand:
    clock = FakeClock(START)
    settings = Settings(
        bot_token="123456:FAKE-TOKEN-for-tests",
        database_url=db_url,
        owner_tg_ids=[OWNER],
        data_dir=tmp_path,
        default_tz="UTC",
    )
    x = FakeX()
    brain = Brain()
    routes = _routes(brain, itertools.cycle(POOL))
    writer, cheap = RoutedLLM("anthropic", routes), RoutedLLM("deepseek", routes)
    judge = RoutedJudge()

    async def x_client(_: UUID) -> Any:
        return x

    engine = create_engine(db_url)  # шлюзу нужна своя фабрика сессий до build_app
    gateway = _gateway(session_factory(engine), clock, writer, cheap, judge)
    app = build_app(
        settings,
        clock=clock,
        bot=telegram.bot,
        gateway=gateway,
        embedder=FakeEmbedder(),
        x_client=x_client,
    )
    dispose = app.dispose

    async def dispose_both() -> None:
        await dispose()
        await engine.dispose()

    app.dispose = dispose_both
    app.bot_deps.ingest_batch = build_ingest_debouncer(app.bot_deps, window_s=0)
    await app.startup()
    return MvpStand(
        app=app,
        telegram=telegram,
        clock=clock,
        x=x,
        owner=Client(build_dispatcher(app.bot_deps), telegram, OWNER),
        writer=writer,
        cheap=cheap,
        judge=judge,
        brain=brain,
    )
