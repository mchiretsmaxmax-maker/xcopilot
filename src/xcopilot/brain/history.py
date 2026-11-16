"""Импорт своей истории при онбординге (ТЗ v3.1, раздел 1.3 п. 5; этап 7A).

Свои посты из X становятся материалами `stance = own`, `origin = history_backfill` с текстом и
сигналом `own_sync`; после чтения считаются исходы (перцентиль среди своих), разбор ставится
в очередь задачами `brain.analyze`. Повторный импорт пропускает уже загруженные посты
(по `external_id`). Бюджет X проверяется до чтения; кончился посреди — прочитанное остаётся.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import structlog

from xcopilot.brain import outcomes, signals
from xcopilot.brain.analyze import ANALYZE_KIND
from xcopilot.brain.features import FeatureValue, compute_features
from xcopilot.clock import Clock
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import BudgetExceeded
from xcopilot.ingest.service import INGEST_KIND
from xcopilot.metrics.events import EventType, emit
from xcopilot.x import cost
from xcopilot.x.client import XClient
from xcopilot.x.models import XPost

log = structlog.get_logger(__name__)

HISTORY_KIND = "brain.history_import"
PER_POST = cost.PRICES[cost.OWNED_POST]


class BudgetLike(Protocol):
    async def check(self, account_id: UUID, scope: str, estimate: int) -> None: ...


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: int
    stopped_by_budget: bool
    text: str


@dataclass(frozen=True)
class _Imported:
    source_id: UUID
    views: int
    features: dict[str, FeatureValue]


class HistoryImporter:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        x_for: Callable[[UUID], Awaitable[XClient]],
        budget: BudgetLike,
        clock: Clock,
    ) -> None:
        self._uow = uow_factory
        self._x_for = x_for
        self._budget = budget
        self._clock = clock

    async def import_own_history(self, account_id: UUID, limit: int = 500) -> ImportResult:
        """Загрузить до `limit` своих постов с метриками; вернуть итог и текст для владельца."""
        try:
            await self._budget.check(account_id, "x_api", PER_POST)
        except BudgetExceeded:
            return self._finish_text(ImportResult(0, 0, True, ""), [])
        x = await self._x_for(account_id)
        imported: list[_Imported] = []
        skipped = 0
        stopped = False
        try:
            async for post in x.iter_own_history(limit):
                item = await self._store(account_id, post)
                if item is None:
                    skipped += 1
                else:
                    imported.append(item)
        except BudgetExceeded:
            stopped = True
            log.info("brain.history.budget", account_id=str(account_id), imported=len(imported))
        await self._outcomes(account_id, imported)
        result = ImportResult(len(imported), skipped, stopped, "")
        async with self._uow() as uow:
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.HISTORY_IMPORTED,
                props={"imported": len(imported), "skipped": skipped, "stopped_by_budget": stopped},
            )
        return self._finish_text(result, imported)

    async def _store(self, account_id: UUID, post: XPost) -> _Imported | None:
        now = self._clock.now()
        async with self._uow() as uow:
            sources = SourcesRepo(uow.session, account_id)
            if await sources.by_external_id(post.id) is not None:
                return None
            kind = "reply" if post.in_reply_to_id else "post"
            src, created = await sources.add_or_get(
                kind=kind,
                stance="own",
                origin="history_backfill",
                content_hash=hashlib.sha256(f"x:{post.id}".encode()).hexdigest(),
                external_id=post.id,
                author_handle=post.author_handle,
                posted_at=post.created_at,
                status="parsed",
                analysis_status="pending",
                meta={"history": True},
            )
            if not created:
                return None
            await sources.add_document(src.id, text=post.text, text_clean=post.text)
            m = post.metrics
            views = (m.impressions if m is not None else None) or 0
            if m is not None:
                await signals.add_signal(
                    uow.session,
                    account_id,
                    src.id,
                    signals.Signal(
                        origin="own_sync",
                        views=m.impressions,
                        likes=m.likes,
                        replies=m.replies,
                        reposts=m.reposts,
                        quotes=m.quotes,
                        bookmarks=m.bookmarks,
                        post_age_hours=_age_hours(post.created_at, now),
                        raw={"x_post_id": post.id},
                    ),
                    observed_at=now,
                )
            jobs = JobsRepo(uow.session)
            # куски, теги и векторы: своя история попадает в паспорт, ретривал и дрейф голоса
            # (этап 14; stance own не проходит проверку на входе, статус parsed → indexed)
            for job_kind in (INGEST_KIND, ANALYZE_KIND):
                await jobs.enqueue(
                    account_id=account_id,
                    kind=job_kind,
                    payload={"source_id": str(src.id)},
                    run_at=now,
                    max_attempts=5,
                    idempotency_key=f"{job_kind}:{src.id}",
                )
            return _Imported(src.id, views, compute_features(post.text, kind))

    async def _outcomes(self, account_id: UUID, items: list[_Imported]) -> None:
        """Исходы после чтения всей истории: перцентиль считается по полной базе."""
        cfg = outcomes.OutcomeConfig.load()
        now = self._clock.now()
        for item in items:
            async with self._uow() as uow:
                await outcomes.compute_outcome(
                    uow.session, account_id, item.source_id, now=now, config=cfg
                )

    @staticmethod
    def _finish_text(result: ImportResult, items: list[_Imported]) -> ImportResult:
        if result.stopped_by_budget and not items:
            text = "Бюджет на чтение X исчерпан — историю не загрузил. Подними лимит в настройках."
        else:
            text = f"Загрузил {result.imported} твоих постов."
            if items:
                ranked = sorted(items, key=lambda i: i.views, reverse=True)
                n = max(1, len(ranked) // 10)
                text += (
                    f" Лучшие 10% — чаще всего {_traits(ranked[:n], best=True)}."
                    f" Худшие 10% — {_traits(ranked[-n:], best=False)}."
                )
            if result.stopped_by_budget:
                text += " Бюджет на чтение X кончился — остальное дочитаю после пополнения."
            text += " Подробнее — /brain"
        return ImportResult(result.imported, result.skipped, result.stopped_by_budget, text)


def _age_hours(posted: datetime | None, now: datetime) -> float | None:
    if posted is None:
        return None
    return max(0.0, (now - posted).total_seconds() / 3600)


def _share(items: list[_Imported], pred: Callable[[dict[str, FeatureValue]], bool]) -> float:
    return sum(pred(i.features) for i in items) / len(items)


def _traits(items: list[_Imported], *, best: bool) -> str:
    """Самые частые признаки группы постов (кодом, до разбора моделью)."""
    checks: list[tuple[str, Callable[[dict[str, FeatureValue]], bool]]] = [
        ("хук с цифрой", lambda f: bool(f["has_number_first_line"])),
        (
            "короткие строки",
            lambda f: int(f["line_count"]) >= 3 and int(f["first_line_len"]) <= 50,
        ),
        ("длинные вступления", lambda f: int(f["first_line_len"]) > 60),
        ("вопрос в конце", lambda f: bool(f["has_question_last_line"])),
        ("ссылки", lambda f: bool(f["has_link"])),
    ]
    found = [name for name, pred in checks if _share(items, pred) >= 0.5]
    if not found:
        return "без явного общего приёма" if best else "без явной общей черты"
    return " и ".join(found[:2])
