"""Контекстный пакет (ТЗ v3.1): секции, бюджет, порядок урезания, воспроизводимость."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.retrieval_stand import FakeKnowledge, Kb, ScriptedEmbedder, default_passport_body, kitem
from xcopilot.retrieval.context_pack import (
    ContextPack,
    PackIds,
    TaskSpec,
    build_pack,
    rehydrate,
)
from xcopilot.retrieval.tokens import estimate_tokens

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
BIG = 1_000_000


def task(topic: str = "L2 комиссии", **kw: Any) -> TaskSpec:
    return TaskSpec(format="post", lang="ru", topic=topic, **kw)


async def make(
    session: AsyncSession,
    kb: Kb,
    acc: Any,
    *,
    topic: str = "L2 комиссии",
    budget: int = 6000,
    knowledge: FakeKnowledge | None = None,
) -> ContextPack:
    return await build_pack(
        session,
        acc,
        task(topic),
        embedder=kb.embedder,
        now=NOW,
        knowledge=knowledge,
        budget_tokens=budget,
    )


def size(pack: ContextPack, **update: Any) -> int:
    return estimate_tokens(pack.model_copy(update=update).render())


async def fill(
    kb: Kb, acc: Any, *, examples: int = 10, knowledge: int = 5, facts: int = 5
) -> FakeKnowledge:
    """База, в которой есть что урезать: примеры, знания, факты, два антипримера."""
    await kb.passport(acc)
    filler = " слово" * 30
    for i in range(examples):
        await kb.chunk(acc, f"L2 пример {i}{filler}", kind="own_post")
    for i in range(facts):
        await kb.fact(acc, f"Факт {i} про L2{filler}", expires_at=NOW + timedelta(days=30))
    await kb.draft(acc, f"Отклонённый черновик один{filler}")
    await kb.draft(acc, f"Отклонённый черновик два{filler}")
    return FakeKnowledge([kitem(i, pad=30) for i in range(knowledge)])


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


async def test_antiexamples_are_own_rejected_drafts(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    await kb.passport(acc)  # антипримеры паспорта в пакет больше не идут
    await kb.draft(acc, "старый отклонённый")
    second = await kb.draft(acc, "средний отклонённый")
    newest = await kb.draft(acc, "новый отклонённый")
    await kb.draft(acc, "одобренный", status="approved")
    await kb.draft(other, "чужой отклонённый")
    pack = await make(session, kb, acc)
    assert [a.draft_id for a in pack.anti_examples] == [newest, second]
    assert "Друзья! Сегодня" not in pack.render()


async def test_pack_respects_token_budget(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc)
    full = await make(session, kb, acc, budget=BIG, knowledge=knowledge)
    assert full.tokens == estimate_tokens(full.render()) > 800
    pack = await make(session, kb, acc, budget=full.tokens // 2, knowledge=knowledge)
    assert pack.tokens <= full.tokens // 2
    assert pack.tokens == estimate_tokens(pack.render())
    assert "over_budget" not in pack.warnings


async def test_pack_trimming_order(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc)
    full = await make(session, kb, acc, budget=BIG, knowledge=knowledge)
    assert (len(full.examples), len(full.knowledge), len(full.facts)) == (10, 5, 5)
    assert len(full.anti_examples) == 2

    async def at(budget: int) -> ContextPack:
        return await make(session, kb, acc, budget=budget, knowledge=knowledge)

    # 1) сначала уходят антипримеры; остальное цело
    p = await at(full.tokens - 1)
    assert len(p.anti_examples) < 2
    assert (len(p.examples), len(p.knowledge), len(p.facts)) == (10, 5, 5)

    # 2) потом знания
    p = await at(size(full, anti_examples=[]) - 1)
    assert p.anti_examples == [] and len(p.knowledge) < 5
    assert (len(p.examples), len(p.facts)) == (10, 5)

    # 3) потом факты
    p = await at(size(full, anti_examples=[], knowledge=[]) - 1)
    assert p.knowledge == [] and len(p.facts) < 5 and len(p.examples) == 10

    # 4) потом примеры (но не меньше четырёх)
    tight = size(full, anti_examples=[], knowledge=[], facts=[]) - 1
    p = await at(tight)
    assert p.facts == [] and 4 <= len(p.examples) < 10
    assert p.tokens <= tight
    assert p.passport is not None  # паспорт не урезается
    assert p.trimmed.get("examples", 0) == 10 - len(p.examples)


async def test_pack_keeps_minimum_four_examples(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc)
    pack = await make(session, kb, acc, budget=1, knowledge=knowledge)
    assert len(pack.examples) == 4
    assert pack.anti_examples == [] and pack.knowledge == [] and pack.facts == []
    assert pack.passport is not None
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


async def test_voice_examples_only_from_own_kinds(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    own = await kb.chunk(acc, "L2 мой пост", kind="own_post")
    await kb.chunk(acc, "L2 понравившийся", kind="liked_sample")
    await kb.chunk(acc, "L2 конкурент", kind="competitor_post")
    for kind in ("note", "file", "chat"):
        await kb.chunk(acc, f"L2 {kind}", kind=kind)
    pack = await make(session, kb, acc)
    assert [c.chunk_id for c in pack.examples] == [own]
    rendered = pack.render()
    assert "L2 конкурент" not in rendered and "L2 понравившийся" not in rendered


async def test_pack_is_scoped_to_account(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    await kb.chunk(other, "L2 чужой пост")
    await kb.fact(other, "чужой факт")
    await kb.passport(other)
    await kb.draft(other, "чужой отклонённый")
    pack = await make(session, kb, acc)
    assert pack.examples == [] and pack.facts == [] and pack.passport is None
    assert pack.anti_examples == []


async def test_pack_is_reproducible_from_ids(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc)
    first = await make(session, kb, acc, budget=2500, knowledge=knowledge)
    second = await make(session, kb, acc, budget=2500, knowledge=knowledge)
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
    restored = await rehydrate(session, acc, PackIds.model_validate(stored), knowledge=knowledge)
    assert restored.model_dump_json() == first.model_dump_json()

    # база ушла вперёд: новые куски, истёкший факт, новый отклонённый — пакет по id прежний
    await kb.chunk(acc, "L2 свежий пост " + "слово " * 5)
    await kb.draft(acc, "ещё один отклонённый")
    await session.execute(text("UPDATE facts SET expires_at = :t"), {"t": NOW - timedelta(days=1)})
    await session.commit()
    again = await rehydrate(
        session, acc, PackIds.model_validate(json.loads(ids.model_dump_json())), knowledge=knowledge
    )
    assert again.model_dump_json() == first.model_dump_json()


async def test_pack_ids_hold_only_ids_and_counts(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc)
    pack = await make(session, kb, acc, knowledge=knowledge)
    ids = pack.to_ids()
    raw = ids.model_dump_json()
    assert "пример" not in raw and "слово" not in raw and "Приём" not in raw
    assert ids.examples == [c.chunk_id for c in pack.examples]
    assert ids.facts == [f.fact_id for f in pack.facts]
    assert ids.knowledge == [k.item_id for k in pack.knowledge]
    assert ids.anti_examples == [a.draft_id for a in pack.anti_examples]
    assert ids.passport_version == 1


async def test_rehydrate_never_leaks_foreign_chunks(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    foreign = await kb.chunk(other, "L2 чужой")
    foreign_draft = await kb.draft(other, "чужой отклонённый")
    ids = PackIds(
        task=task("тема"), budget_tokens=6000, examples=[foreign], anti_examples=[foreign_draft]
    )
    pack = await rehydrate(session, acc, ids)
    assert pack.examples == [] and pack.anti_examples == []


async def test_empty_base_returns_pack_with_warning(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    pack = await make(session, kb, acc)
    assert pack.examples == [] and pack.facts == [] and pack.knowledge == []
    assert pack.passport is None
    assert "empty_knowledge_base" in pack.warnings
    assert "no_active_passport" in pack.warnings
    assert pack.tokens == estimate_tokens(pack.render())


async def test_build_pack_emits_event(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    knowledge = await fill(kb, acc, examples=5, knowledge=2, facts=1)
    pack = await make(session, kb, acc, knowledge=knowledge)
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
    assert props["facts"] == 1 and props["knowledge"] == 2 and props["budget_tokens"] == 6000


async def test_topic_steers_examples(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    for i in range(14):
        await kb.chunk(acc, f"про борщ {i}")
    target = await kb.chunk(acc, "блобы и L2 после Dencun")
    pack = await make(session, kb, acc, topic="блобы и L2 после Dencun")
    assert pack.examples[0].chunk_id == target
    assert len(pack.examples) == 10


# --- ТЗ v3.1, этап 9 (дельта) -----------------------------------------------------------


async def test_context_pack_has_knowledge_section_from_provider(
    kb: Kb, session: AsyncSession
) -> None:
    acc = await kb.account("a")
    provider = FakeKnowledge([kitem(1, slot="pinned"), kitem(2, slot="hook"), kitem(3)])
    pack = await make(session, kb, acc, knowledge=provider)
    assert [k.item_id for k in pack.knowledge] == [k.item_id for k in provider.items]
    assert provider.tasks == [task()]  # провайдеру передали именно задачу
    rendered = pack.render()
    assert "# Знания" in rendered and "Приём 2" in rendered and "Утверждение 3" in rendered
    # id знаний хранятся, и по ним пакет восстанавливается тем же провайдером
    again = await rehydrate(session, acc, pack.to_ids(), knowledge=provider)
    assert again.model_dump_json() == pack.model_dump_json()


async def test_context_pack_has_no_foreign_text_sections(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    glob = await kb.global_account()
    await kb.chunk(acc, "L2 мой пост")
    await kb.chunk(acc, "L2 текст конкурента ZZZ", kind="competitor_post")
    await kb.chunk(acc, "L2 понравившийся ZZZ", kind="liked_sample")
    await kb.chunk(acc, "L2 заметка ZZZ", kind="note")
    await kb.lesson(acc, "урок ZZZ")
    await kb.lesson(glob, "глобальный урок ZZZ")
    pack = await make(session, kb, acc, knowledge=FakeKnowledge([kitem(1)]))
    dumped = pack.model_dump_json() + pack.to_ids().model_dump_json() + pack.render()
    assert "ZZZ" not in dumped
    assert not hasattr(pack, "references") and not hasattr(pack, "lessons")
    assert "# Референсы" not in dumped and "# Уроки" not in dumped


async def test_voice_examples_only_own_stance(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    other = await kb.account("b")
    own = [await kb.chunk(acc, f"L2 мой пост {i}") for i in range(3)]
    for kind in ("liked_sample", "competitor_post", "note", "file", "chat"):
        await kb.chunk(acc, f"L2 {kind} чужой голос", kind=kind)
    await kb.chunk(other, "L2 свой пост другого аккаунта")
    pack = await make(session, kb, acc)
    assert {c.chunk_id for c in pack.examples} == set(own)
    assert {c.kind for c in pack.examples} == {"own_post"}


async def test_budget_split_by_section(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await kb.passport(acc)
    big = " слово" * 120
    for i in range(10):
        await kb.chunk(acc, f"L2 пример {i}{big}")
    for i in range(8):
        await kb.fact(acc, f"Факт {i}{big}", expires_at=NOW + timedelta(days=9))
    for i in range(2):
        await kb.draft(acc, f"Отклонённый {i}{big}")
    provider = FakeKnowledge([kitem(i, pad=120) for i in range(15)])
    pack = await make(session, kb, acc, knowledge=provider)
    by_section = pack.section_tokens()
    assert by_section["examples"] <= 1600
    assert by_section["knowledge"] <= 1500
    assert by_section["facts"] <= 800
    assert by_section["anti_examples"] <= 300
    assert pack.tokens <= 6000
    # секции урезаны, но не обнулены: у каждой есть содержимое
    assert pack.examples and pack.knowledge and pack.facts and pack.anti_examples
    assert pack.trimmed  # что-то действительно урезано по потолкам


async def test_pack_works_with_empty_knowledge(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    await kb.passport(acc)
    await kb.chunk(acc, "L2 мой пост")
    for provider in (None, FakeKnowledge([])):
        pack = await make(session, kb, acc, knowledge=provider)
        assert pack.knowledge == []
        assert "# Знания" not in pack.render()
        assert pack.passport is not None and len(pack.examples) == 1
