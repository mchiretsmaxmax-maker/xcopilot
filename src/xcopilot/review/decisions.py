"""Решения владельца по черновику (ТЗ, этап 12, задачи 4–8, 14).

Каждое решение — строка `reviews`, событие `review.*` (метрики zero_edit_rate, approval_rate,
отказы по причинам) и доказательства совместимости применённых знаний
(`brain.evidence.record_from_review`, раздел Б2.4). Повторное нажатие не создаёт второго
решения (строка черновика блокируется), устаревшая карточка — `CardExpired`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from xcopilot.brain.config import BrainSettings
from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.quality.lint.engine import LintReport
from xcopilot.review.settings import ReviewSettings


class CardExpired(Exception):
    """Карточка старше 48 часов: кнопки больше не действуют."""


class Relinter(Protocol):
    """Повторный прогон линтера по исправленному тексту (QualityGate.relint)."""

    async def relint(
        self, account_id: UUID, draft_id: UUID, text: str, parts: Sequence[str]
    ) -> LintReport: ...


@dataclass(frozen=True)
class DecisionResult:
    draft_id: UUID
    status: str
    review_id: UUID | None = None
    repeated: bool = False  # решение уже было: ничего не записано
    distance: float | None = None
    warnings: tuple[str, ...] = ()  # ошибки линтера правки — как предупреждения


def edit_distance(before: str, after: str) -> float:
    """Нормированная дистанция Левенштейна 0..1."""
    raise NotImplementedError


class Decisions:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        queue: JobQueue,
        *,
        relinter: Relinter | None = None,
        settings: ReviewSettings | None = None,
        brain_config: BrainSettings | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._queue = queue
        self._relinter = relinter
        self._settings = settings or ReviewSettings()
        self._brain_config = brain_config

    async def approve(self, account_id: UUID, draft_id: UUID, user_id: UUID) -> DecisionResult:
        raise NotImplementedError

    async def edit(
        self, account_id: UUID, draft_id: UUID, user_id: UUID, new_text: str
    ) -> DecisionResult:
        raise NotImplementedError

    async def reject(
        self,
        account_id: UUID,
        draft_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> DecisionResult:
        raise NotImplementedError

    async def regenerate(
        self, account_id: UUID, draft_id: UUID, user_id: UUID, hint: str | None = None
    ) -> UUID:
        """Новая генерация по той же задаче с подсказкой; вернуть id задачи writer.generate."""
        raise NotImplementedError
