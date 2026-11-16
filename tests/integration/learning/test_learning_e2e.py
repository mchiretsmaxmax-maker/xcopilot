"""Этап 13, сквозной: две правки → правило → «да» → следующий пакет содержит знание."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tests.learning_stand import lexicon_candidate, make_learn, result
from xcopilot.brain import api, owner
from xcopilot.brain.schemas import TaskSpec
from xcopilot.learning.from_reviews import propose


async def test_two_edits_then_confirm_then_next_pack_contains_item(session: AsyncSession) -> None:
    L = await make_learn(session)
    await L.review()
    await L.review(before="Let's break down why gas is cheap now", after="Why gas is cheap now")
    L.llm.push(result(lexicon_candidate()))
    await propose(L.ctx)
    (item,) = await L.edit_items()
    topic = f"{item['statement']} Не начинай пост с «let's»"
    task = TaskSpec(format="post", lang="en", topic=topic, include_probation=False)

    async def pack_ids() -> list[object]:
        pack = await api.build_pack(
            session, L.acc, task, embedder=L.ctx.embedder, clock=L.ctx.clock, record=False
        )
        return [i.item_id for i in pack.items]

    assert item["id"] not in await pack_ids()  # без «да» в черновики не идёт
    await owner.confirm(L.ctx, item["id"], L.uid)
    assert item["id"] in await pack_ids()
