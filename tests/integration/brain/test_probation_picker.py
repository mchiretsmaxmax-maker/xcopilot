"""Выбор испытуемого знания для варианта 🧪 (этапы 10/9A): BrainProbationPicker."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW, knowledge, new_account
from tests.fakes import FakeClock
from tests.integration.db.brain_helpers import draft, insert, pack
from xcopilot.brain.probation import BrainProbationPicker
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo, KnowledgeLinksRepo
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.probation import ProbationPicker

TASK = TaskSpec(format="post", lang="ru", topic="про провалы")


async def test_picks_least_tested_matching_probation_item(session: AsyncSession) -> None:
    acc = await new_account(session)
    picker: ProbationPicker = BrainProbationPicker(FakeClock(NOW))
    assert await picker.pick(session, acc, TASK) is None
    tested = await knowledge(
        session, acc, status="probation", probation_started_at=NOW - timedelta(days=9)
    )
    fresh = await knowledge(
        session, acc, status="probation", probation_started_at=NOW - timedelta(days=2)
    )
    older = await knowledge(
        session, acc, status="probation", probation_started_at=NOW - timedelta(days=5)
    )
    thread_only = await knowledge(
        session, acc, status="probation", probation_started_at=NOW - timedelta(days=30)
    )
    await KnowledgeItemsRepo(session, acc).set_scopes(thread_only, {"format": ["thread"]})
    await knowledge(session, acc, status="active")
    d, p = await draft(session, acc), await pack(session, acc)
    await insert(
        session,
        "knowledge_applications",
        account_id=acc,
        pack_id=p,
        item_id=tested,
        draft_id=d,
        slot="probation",
        created_at=NOW - timedelta(days=1),
    )
    got = await picker.pick(session, acc, TASK)
    assert got is not None and (got.item_id, got.slot) == (older, "probation")
    # открытое противоречие убирает знание из выбора
    await KnowledgeLinksRepo(session, acc).add(
        from_item_id=older,
        to_item_id=fresh,
        relation="contradicts",
        confidence=0.8,
        decided_by="judge",
    )
    got = await picker.pick(session, acc, TASK)
    assert got is not None and got.item_id == tested
    assert (
        await picker.pick(session, acc, TASK.model_copy(update={"include_probation": False}))
        is None
    )
