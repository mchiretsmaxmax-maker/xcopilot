"""Детектор применённых знаний на детекторах «Мозга» (ТЗ v3.1, этап 11, задача 12).

Рубрики (`detector.type = rubric`) — судьёй через `brain.detectors.eval_rubrics` на нужной
части текста: ≥ rubric_present — применено, ≤ rubric_absent — нет, между — не отмечается.
Feature-детекторы смотрят на поля разбора материала; у черновика разбора нет — пока
пропускаются (ADR 0021), `applied` для таких знаний остаётся NULL.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from xcopilot.brain.config import BrainSettings, load_brain_config
from xcopilot.brain.context import BrainContext, BrainLLM
from xcopilot.brain.detectors import PARTS, Part, RubricItem, eval_rubrics
from xcopilot.brain.schemas import DETECTOR, RubricDetector
from xcopilot.clock import Clock
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.uow import DbSession
from xcopilot.quality.attribution import AppliedHit


class BrainRubricDetector:
    """AppliedDetector: рубрики знаний пакета — судьёй, по одному вызову на часть текста."""

    def __init__(self, llm: BrainLLM, clock: Clock, config: BrainSettings | None = None) -> None:
        self._llm = llm
        self._clock = clock
        self._config = config or load_brain_config()

    async def detect(
        self, session: DbSession, account_id: UUID, text: str, item_ids: Sequence[UUID]
    ) -> dict[UUID, AppliedHit]:
        wanted = set(item_ids)
        by_part: dict[Part, list[RubricItem]] = {p: [] for p in PARTS}
        for row in await BrainRepo(session, account_id).detector_items():
            if row["id"] not in wanted:
                continue
            det = DETECTOR.validate_python(row["detector"])
            if isinstance(det, RubricDetector):
                by_part[det.on].append(RubricItem(row["id"], det.question))
        if not any(by_part.values()):
            return {}
        ctx = BrainContext(
            session=session,
            account_id=account_id,
            clock=self._clock,
            llm=self._llm,
            config=self._config,
        )
        cfg = self._config.detectors
        hits: dict[UUID, AppliedHit] = {}
        for part, items in by_part.items():
            if not items:
                continue
            for item_id, p in (await eval_rubrics(ctx, items, text, part)).items():
                if p >= cfg.rubric_present:
                    hits[item_id] = AppliedHit(applied=True, score=p, by="rubric")
                elif p <= cfg.rubric_absent:
                    hits[item_id] = AppliedHit(applied=False, score=p, by="rubric")
        return hits
