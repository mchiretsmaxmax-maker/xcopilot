"""Испытуемое знание для варианта 🧪 (ТЗ v3.1: этап 10, задача 12; выбор — этап 9A).

`BrainProbationPicker` реализует `writer.probation.ProbationPicker`. Он берёт знание на испытании,
допущенное к применению (не отключено, без открытого противоречия) и подходящее задаче по области
действия. Из них выбирается наименее проверенное: меньше всего применений за 14 дней,
при равенстве — дольше всех на испытании.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from xcopilot.clock import Clock, SystemClock
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.uow import DbSession
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec

WINDOW = timedelta(days=14)


class BrainProbationPicker:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def pick(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> KnowledgeItem | None:
        if not task.include_probation:
            return None
        facets = {"format": task.format, "goal": task.goal, "lang": task.lang}
        if task.niche:
            facets["niche"] = task.niche
        if task.archetype:
            facets["archetype"] = task.archetype
        rows = await BrainRepo(session, account_id).probation_candidates(
            facets, since=self._clock.now() - WINDOW
        )
        if not rows:
            return None
        top = rows[0]
        return KnowledgeItem(
            item_id=top["id"],
            slot="probation",
            guidance=top["guidance"],
            statement=top["statement"],
            skeleton=top["skeleton"],
        )
