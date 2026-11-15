"""e2e этапа 9A: 30 материалов → кандидаты с доказательствами."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import (
    FakeBrainLLM,
    candidate_judge,
    make_ctx,
    material,
    new_account,
    new_author,
)
from tests.integration.brain.extract_helpers import candidate, result
from xcopilot.brain import extract
from xcopilot.brain.evidence import record_from_material
from xcopilot.brain.scoring import recompute


async def test_thirty_materials_produce_candidates_with_evidence(session: AsyncSession) -> None:
    acc = await new_account(session)
    authors = [await new_author(session, acc) for _ in range(6)]
    srcs = [await material(session, acc, author_id=authors[i % 6]) for i in range(30)]
    closer = candidate(
        kind="thesis",
        element="closer",
        statement="Вопрос к читателю в последней строке даёт больше ответов",
        guidance="Закончи пост коротким вопросом к читателю",
        detector={"type": "feature", "all": [{"path": "closer", "op": "eq", "value": "question"}]},
        supporting=["M1", "M2", "M3"],
    )
    llm = FakeBrainLLM(judge=candidate_judge()).push(
        result(candidate(supporting=["M1", "M2", "M3", "M4"]), closer), result()
    )
    ctx = make_ctx(session, acc, llm=llm)
    created = await extract.run(ctx)
    assert len(created) == 2
    assert len(llm.gen_calls) == 2  # пачки 25 + 5
    for sid in srcs:
        await record_from_material(ctx, sid)
    await recompute(ctx)
    rows = (
        await session.execute(
            text(
                "SELECT i.id, i.status, i.evidence_n, i.independent_authors,"
                " (SELECT count(*) FROM knowledge_evidence e WHERE e.item_id = i.id) AS ev"
                " FROM knowledge_items i WHERE i.account_id = :a"
            ),
            {"a": acc},
        )
    ).all()
    assert len(rows) == 2
    for r in rows:
        assert r.status in ("candidate", "probation")
        assert r.ev >= 30  # каждый из 30 материалов — доказательство, без дублей
        assert r.independent_authors == 6
