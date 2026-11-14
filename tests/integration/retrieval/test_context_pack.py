"""Контекстный пакет: секции, бюджет, порядок урезания, воспроизводимость."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.retrieval_stand import Kb, ScriptedEmbedder, default_passport_body
from xcopilot.retrieval.context_pack import (
    ContextPack,
    PackIds,
    build_pack,
    rehydrate,
)
from xcopilot.retrieval.tokens import estimate_tokens

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
BIG = 1_000_000


async def make(
    session: AsyncSession, kb: Kb, acc: Any, *, topic: str = "L2 комиссии", budget: int = 6000
) -> ContextPack:
    return await build_pack(
        session, acc, topic, "post", embedder=kb.embedder, now=NOW, budget_tokens=budget
    )


def size(pack: ContextPack, **update: Any) -> int:
    return estimate_tokens(pack.model_copy(update=update).render())


async def fill(kb: Kb, acc: Any, *, examples: int = 12, refs: int = 5, facts: int = 5) -> None:
    """База, в которой есть что урезать: длинные примеры, референсы, факты, два антипримера."""
    await kb.passport(acc)
    filler = " слово" * 30
    for i in range(examples):
        await kb.chunk(acc, f"L2 пример {i}{filler}", kind="own_post")
    for i in range(refs):
        await kb.chunk(acc, f"L2 референс {i}{filler}", kind="competitor_post")
    for i in range(facts):
        await kb.fact(acc, f"Факт {i} про L2{filler}", expires_at=NOW + timedelta(days=30))
    await kb.lesson(acc, "Не начинай с приветствия")


async def test_pack_always_contains_passport_and_hard_rules(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    pid = await kb.passport(acc, version=3)
    await kb.chunk(acc, "L2 пост " + "слово " * 200)
    for budget in (6000, 1):  # и при отчаянно малом бюджете
        pack = await make(session, kb, acc, budget=budget)
        assert pack.passport_id == pid and pack.passport_version == 3
        assert pack.passport is not None
        assert pack.passport["summary"] == default_passport_body()["summary"]
        assert pack.hard_rules == default_passport_body()["hard_rules"]
        assert "casing" in pack.render()


async def test_pack_has_two_antiexamples_from_passport(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await kb.passport(acc)
    pack = await make(session, kb, acc)
    assert pack.anti_examples == default_passport_body()["anti_examples"][:2]


async def test_pack_respects_token_budget(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc)
    full = await make(session, kb, acc, budget=BIG)
    assert full.tokens == estimate_tokens(full.render()) > 800
    pack = await make(session, kb, acc, budget=full.tokens // 2)
    assert pack.tokens <= full.tokens // 2
    assert pack.tokens == estimate_tokens(pack.render())
    assert "over_budget" not in pack.warnings


async def test_pack_trimming_order(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc)
    full = await make(session, kb, acc, budget=BIG)
    assert (len(full.examples), len(full.references), len(full.facts)) == (12, 5, 5)
    assert len(full.anti_examples) == 2 and len(full.lessons) == 1

    # 1) сначала уходят антипримеры; остальное цело
    p = await make(session, kb, acc, budget=full.tokens - 1)
    assert len(p.anti_examples) < 2
    assert (len(p.examples), len(p.references), len(p.facts)) == (12, 5, 5)

    # 2) потом референсы
    p = await make(session, kb, acc, budget=size(full, anti_examples=[]) - 1)
    assert p.anti_examples == [] and len(p.references) < 5
    assert (len(p.examples), len(p.facts)) == (12, 5)

    # 3) потом факты
    p = await make(session, kb, acc, budget=size(full, anti_examples=[], references=[]) - 1)
    assert p.anti_examples == [] and p.references == [] and len(p.facts) < 5
    assert len(p.examples) == 12

    # 4) потом примеры (но не меньше четырёх)
    tight = size(full, anti_examples=[], references=[], facts=[]) - 1
    p = await make(session, kb, acc, budget=tight)
    assert p.facts == [] and 4 <= len(p.examples) < 12
    assert p.tokens <= tight
    # уроки и паспорт не урезаются
    assert len(p.lessons) == 1 and p.passport is not None
    # что урезано — записано
    assert p.trimmed.get("examples", 0) == 12 - len(p.examples)


async def test_pack_keeps_minimum_four_examples(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc)
    pack = await make(session, kb, acc, budget=1)
    assert len(pack.examples) == 4
    assert pack.anti_examples == [] and pack.references == [] and pack.facts == []
    assert pack.passport is not None and len(pack.lessons) == 1
    assert "over_budget" in pack.warnings


async def test_pack_excludes_expired_facts(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await kb.fact(acc, "просрочен", expires_at=NOW - timedelta(seconds=1))
    await kb.fact(acc, "ровно сейчас", expires_at=NOW)
    ok_future = await kb.fact(acc, "ещё годен", expires_at=NOW + timedelta(days=1))
    ok_forever = await kb.fact(acc, "бессрочный", expires_at=None)
    pack = await make(session, kb, acc)
    assert {f.fact_id for f in pack.facts} == {ok_future, ok_forever}
    assert all(f.source_url for f in pack.facts)


async def test_pack_includes_only_accepted_lessons(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    ok = await kb.lesson(acc, "принятый")
    for status in ("proposed", "rejected", "retired"):
        await kb.lesson(acc, f"урок {status}", status=status)
    pack = await make(session, kb, acc)
    assert [x.lesson_id for x in pack.lessons] == [ok]
    assert pack.lessons[0].scope == "account"


async def test_pack_includes_global_lessons(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("other")
    glob = await kb.global_account()
    mine = await kb.lesson(acc, "свой", weight=1.0)
    g = await kb.lesson(glob, "глобальный анти-AI", weight=2.0)
    g_prop = await kb.lesson(glob, "глобальный непринятый", status="proposed")
    foreign = await kb.lesson(other, "чужой аккаунт", weight=9.0)
    pack = await make(session, kb, acc)
    ids = [x.lesson_id for x in pack.lessons]
    assert ids == [g, mine]  # по весу
    assert g_prop not in ids and foreign not in ids
    assert {x.lesson_id: x.scope for x in pack.lessons} == {g: "global", mine: "account"}


async def test_pack_takes_at_most_twenty_lessons_by_weight(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    ids = [await kb.lesson(acc, f"урок {i}", weight=float(i)) for i in range(25)]
    pack = await make(session, kb, acc)
    assert [x.lesson_id for x in pack.lessons] == list(reversed(ids[5:]))


async def test_voice_examples_only_from_own_kinds(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    own = await kb.chunk(acc, "L2 мой пост", kind="own_post")
    liked = await kb.chunk(acc, "L2 понравившийся", kind="liked_sample")
    comp = await kb.chunk(acc, "L2 конкурент", kind="competitor_post")
    for kind in ("note", "file", "chat"):
        await kb.chunk(acc, f"L2 {kind}", kind=kind)
    pack = await make(session, kb, acc)
    assert [c.chunk_id for c in pack.examples] == [own]
    assert {c.chunk_id for c in pack.references} == {liked, comp}
    assert {c.kind for c in pack.references} == {"liked_sample", "competitor_post"}
    assert pack.render().index("L2 мой пост") < pack.render().index("L2 конкурент")


async def test_pack_is_scoped_to_account(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    await kb.chunk(other, "L2 чужой пост")
    await kb.fact(other, "чужой факт")
    await kb.passport(other)
    pack = await make(session, kb, acc)
    assert pack.examples == [] and pack.facts == [] and pack.passport is None


async def test_pack_is_reproducible_from_ids(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc)
    glob = await kb.global_account()
    await kb.lesson(glob, "глобальный")
    first = await make(session, kb, acc, budget=2500)
    second = await make(session, kb, acc, budget=2500)
    assert first.model_dump_json() == second.model_dump_json()  # побайтно

    # id кладутся в generation_runs.context_pack и читаются обратно
    ids = first.to_ids()
    run_id = (
        await session.execute(
            text(
                "INSERT INTO generation_runs (account_id, kind, context_pack, passport_version) "
                "VALUES (:a, 'post', CAST(:c AS jsonb), :v) RETURNING id"
            ),
            {"a": acc, "c": ids.model_dump_json(), "v": first.passport_version},
        )
    ).scalar_one()
    stored = (
        await session.execute(
            text("SELECT context_pack FROM generation_runs WHERE id = :i"), {"i": run_id}
        )
    ).scalar_one()
    restored = await rehydrate(session, acc, PackIds.model_validate(stored))
    assert restored.model_dump_json() == first.model_dump_json()

    # база ушла вперёд: новые куски, истёкший факт, новый урок — пакет по id прежний
    await kb.chunk(acc, "L2 свежий пост " + "слово " * 5)
    await kb.lesson(acc, "новый урок")
    await session.execute(text("UPDATE facts SET expires_at = :t"), {"t": NOW - timedelta(days=1)})
    await session.commit()
    again = await rehydrate(session, acc, PackIds.model_validate(json.loads(ids.model_dump_json())))
    assert again.model_dump_json() == first.model_dump_json()


async def test_pack_ids_hold_only_ids_and_counts(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc)
    pack = await make(session, kb, acc)
    raw = pack.to_ids().model_dump_json()
    assert "пример" not in raw and "слово" not in raw  # текстов нет, только id
    ids = pack.to_ids()
    assert ids.examples == [c.chunk_id for c in pack.examples]
    assert ids.facts == [f.fact_id for f in pack.facts]
    assert ids.passport_version == 1 and ids.anti_examples == len(pack.anti_examples)


async def test_rehydrate_never_leaks_foreign_chunks(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    foreign = await kb.chunk(other, "L2 чужой")
    ids = PackIds(topic="t", fmt="post", budget_tokens=6000, examples=[foreign])
    pack = await rehydrate(session, acc, ids)
    assert pack.examples == []


async def test_empty_base_returns_pack_with_warning(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    pack = await make(session, kb, acc)
    assert pack.examples == [] and pack.facts == [] and pack.lessons == []
    assert pack.references == [] and pack.passport is None
    assert "empty_knowledge_base" in pack.warnings
    assert "no_active_passport" in pack.warnings
    assert pack.tokens == estimate_tokens(pack.render())


async def test_build_pack_emits_event(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await fill(kb, acc, examples=5, refs=1, facts=1)
    pack = await make(session, kb, acc)
    await session.commit()
    rows = (
        await session.execute(
            text(
                "SELECT props FROM events WHERE account_id = :a AND type = 'retrieval.pack_built'"
            ),
            {"a": acc},
        )
    ).all()
    assert len(rows) == 1
    props = rows[0][0]
    assert props["tokens"] == pack.tokens and props["examples"] == 5
    assert props["facts"] == 1 and props["budget_tokens"] == 6000


async def test_topic_steers_examples(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    for i in range(14):
        await kb.chunk(acc, f"про борщ {i}")
    target = await kb.chunk(acc, "блобы и L2 после Dencun")
    pack = await make(session, kb, acc, topic="блобы и L2 после Dencun")
    assert pack.examples[0].chunk_id == target
    assert len(pack.examples) == 12
