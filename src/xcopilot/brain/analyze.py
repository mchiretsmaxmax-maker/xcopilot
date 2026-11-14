"""Разбор материала ролью cheap (ТЗ v3.1, шаги 3–6 конвейера; этап 7A).

Берёт только `sources.ready_for_analysis()`; материал идёт в промпт как недоверенные данные;
цитаты проверяет код (`verify_quotes`); неизвестный ключ словаря заменяется ближайшим и
уходит в предложения. Переразбор создаёт новую текущую строку, старая остаётся в истории.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from xcopilot.brain.config import BrainConfig
from xcopilot.brain.schemas import MaterialAnalysis
from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.gateway import LLMGateway

PROMPT_KEY = "brain.analyze_material.v1"
ANALYZE_KIND = "brain.analyze"
PURPOSE = "brain.analyze"
REANALYZE_PURPOSE = "brain.reanalyze"


class Analyzer:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        clock: Clock,
        *,
        notifier: object | None = None,
        config: BrainConfig | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._clock = clock
        self._notifier = notifier
        self._config = config

    async def analyze(self, account_id: UUID, source_id: UUID) -> MaterialAnalysis | None:
        raise NotImplementedError

    async def reanalyze_outdated(self, account_id: UUID, limit: int = 50) -> int:
        raise NotImplementedError
