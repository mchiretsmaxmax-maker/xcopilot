"""Пакет знаний на настоящей БД (этап 9B): отбор, слоты, испытание, воспроизводимость, экспорт."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW, make_ctx, material, new_account
from tests.fakes import FakeClock
from tests.integration.db.brain_helpers import draft, evidence, insert, pack
from tests.pack_stand import build, embedder, kitem, task
from tests.retrieval_stand import axis
from xcopilot.brain import api, snapshots
from xcopilot.brain.export import export_full, export_pack
from xcopilot.brain.probation import BrainProbationPicker
from xcopilot.brain.render import to_json, to_markdown
from xcopilot.brain.schemas import ItemCard, KnowledgePack
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo, KnowledgeLinksRepo
from xcopilot.retrieval.tokens import estimate_tokens


def ids(p: KnowledgePack) -> set[UUID]:
    return {i.item_id for i in p.items}


async def test_pack_respects_token_budget(session: AsyncSession) -> None:
    acc = await new_account(session)
    for k in range(8):
        await kitem(session, acc, guidance=f"Длинный совет номер {k} " + "слово " * 20)
        await kitem(session, acc, kind="thesis", element="device", guidance="Приём " + "ага " * 30)
    pk = await build(session, acc, task(budget_tokens=400))
    assert pk.items
    assert pk.token_count <= 400
    assert pk.token_count == estimate_tokens(to_markdown(pk))


async def test_slots_filled_by_element_and_kind(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(4):
        await kitem(session, acc)
        await kitem(session, acc, kind="thesis", element="device")
        await kitem(session, acc, kind="anti_pattern", element="general")
    for _ in range(3):
        await kitem(session, acc, kind="structure", element="structure")
        await kitem(session, acc, kind="thesis", element="general", layer="platform")
    await kitem(session, acc, kind="thesis", element="closer")
    pk = await build(session, acc, task(budget_tokens=4000))
    hooks = sum(1 for i in pk.items if i.slot == "core" and i.element == "hook")
    assert hooks == 3
    assert sum(1 for i in pk.items if i.kind == "structure") == 2
    assert sum(1 for i in pk.items if i.element == "device") == 3
    assert sum(1 for i in pk.items if i.element == "closer") == 1
    assert sum(1 for i in pk.items if i.layer == "platform") == 2
    assert sum(1 for i in pk.items if i.slot == "anti") == 3
    assert len(pk.items) <= 15


async def test_reply_task_uses_reply_slots(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(4):
        await kitem(session, acc)
    for _ in range(3):
        await kitem(session, acc, kind="structure", element="structure")
        await kitem(session, acc, kind="thesis", element="general", layer="platform")
    pk = await build(session, acc, task("reply", budget_tokens=4000))
    assert sum(1 for i in pk.items if i.element == "hook") == 2
    assert sum(1 for i in pk.items if i.kind == "structure") == 1
    assert sum(1 for i in pk.items if i.layer == "platform") == 1


async def test_scope_filter_excludes_other_format_and_lang(session: AsyncSession) -> None:
    acc = await new_account(session)
    keep = await kitem(session, acc, scopes={"format": ["post"], "lang": ["ru"]})
    free = await kitem(session, acc)
    thread = await kitem(session, acc, scopes={"format": ["thread"]})
    english = await kitem(session, acc, scopes={"lang": ["en"]})
    got = ids(await build(session, acc))
    assert {keep, free} <= got
    assert not {thread, english} & got


async def test_pinned_included_first_max_5(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(7):
        await kitem(session, acc, kind="thesis", element="device", owner_state="pinned")
    await kitem(session, acc)
    off_scope = await kitem(session, acc, owner_state="pinned", scopes={"format": ["thread"]})
    pk = await build(session, acc, task(budget_tokens=4000))
    assert [i.slot for i in pk.items[:5]] == ["pinned"] * 5
    assert sum(i.slot == "pinned" for i in pk.items) == 5
    assert off_scope not in ids(pk)


async def test_disabled_and_conflicted_never_included(session: AsyncSession) -> None:
    acc = await new_account(session)
    ok = await kitem(session, acc)
    off = await kitem(session, acc, owner_state="disabled")
    conflicted = await kitem(session, acc, status="conflicted")
    candidate = await kitem(session, acc, status="candidate")
    archived = await kitem(session, acc, status="archived")
    got = ids(await build(session, acc))
    assert got == {ok}
    assert not {off, conflicted, candidate, archived} & got


async def test_contradicting_items_never_together(session: AsyncSession) -> None:
    acc = await new_account(session)
    a = await kitem(session, acc)
    b = await kitem(session, acc)
    free = await kitem(session, acc)
    await KnowledgeLinksRepo(session, acc).add(
        from_item_id=a, to_item_id=b, relation="contradicts", confidence=0.9, decided_by="judge"
    )
    got = ids(await build(session, acc))
    assert got == {free}


async def test_duplicates_never_together(session: AsyncSession) -> None:
    acc = await new_account(session)
    a = await kitem(session, acc, quality=0.7)
    b = await kitem(session, acc, quality=0.5)
    await KnowledgeLinksRepo(session, acc).add(
        from_item_id=a, to_item_id=b, relation="duplicates", confidence=0.9, decided_by="judge"
    )
    assert ids(await build(session, acc)) == {a}


async def test_probation_slot_single_and_rotates(session: AsyncSession) -> None:
    acc = await new_account(session)
    await kitem(session, acc)
    trial = [
        await kitem(session, acc, status="probation", probation_started_at=NOW - timedelta(days=d))
        for d in (9, 5, 2)
    ]
    picker = BrainProbationPicker(FakeClock(NOW))
    seen: list[UUID] = []
    for _ in range(3):
        pk = await build(session, acc, probation=picker)
        slots = [i for i in pk.items if i.slot == "probation"]
        assert len(slots) == 1
        assert pk.items[-1].slot == "probation"
        seen.append(slots[0].item_id)
        d, p = await draft(session, acc), await pack(session, acc)
        await insert(
            session,
            "knowledge_applications",
            account_id=acc,
            pack_id=p,
            item_id=slots[0].item_id,
            draft_id=d,
            slot="probation",
            created_at=NOW - timedelta(hours=1),
        )
    assert set(seen) == set(trial)
    # знание на испытании никогда не занимает обычные слоты
    assert all(i.slot == "probation" for i in pk.items if i.item_id in trial)


async def test_probation_excluded_when_flag_off(session: AsyncSession) -> None:
    acc = await new_account(session)
    await kitem(session, acc)
    trial = await kitem(session, acc, status="probation")
    pk = await build(session, acc, task(include_probation=False), probation=BrainProbationPicker())
    assert trial not in ids(pk)
    assert all(i.slot != "probation" for i in pk.items)


async def test_pack_reproducible_from_stored_ids(session: AsyncSession) -> None:
    acc = await new_account(session)
    first = await kitem(session, acc, guidance="Начни с цифры")
    for _ in range(3):
        await kitem(session, acc, kind="thesis", element="device")
    one = await build(session, acc)
    again = await build(session, acc)
    assert to_markdown(one) == to_markdown(again)
    assert [i.model_dump() for i in one.items] == [i.model_dump() for i in again.items]
    await KnowledgeItemsRepo(session, acc).edit(
        first, changed_by="system", reason="edit", guidance="Совсем другой текст"
    )
    restored = await api.load_pack(session, acc, one.pack_id)
    assert [i.model_dump() for i in restored.items] == [i.model_dump() for i in one.items]
    assert to_markdown(restored) == to_markdown(one)
    exp = await api.explain(session, acc, one.pack_id)
    assert [e.item_id for e in exp.items] == [i.item_id for i in one.items]
    assert exp.consumer == "writer"


async def test_render_identical_for_every_consumer(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(3):
        await kitem(session, acc)
    await kitem(session, acc, kind="anti_pattern", element="general")
    packs = [await build(session, acc, consumer=c) for c in ("writer", "export", "mcp", "eval")]
    assert len({to_markdown(p) for p in packs}) == 1
    assert len({json.dumps([i.model_dump(mode="json") for i in p.items]) for p in packs}) == 1
    rows = (
        await session.execute(
            text("SELECT consumer FROM knowledge_packs WHERE account_id = :a ORDER BY created_at"),
            {"a": acc},
        )
    ).all()
    assert sorted(r[0] for r in rows) == ["eval", "export", "mcp", "writer"]


async def test_pack_contains_no_foreign_verbatim_text_in_db(session: AsyncSession) -> None:
    """Знание, дословно повторяющее 6 слов чужого материала-источника, в пакет не попадает."""
    acc = await new_account(session)
    foreign = "ставка на длинную дистанцию всегда побеждает быструю прибыль в итоге"
    src = await material(session, acc, stance="inspiration", text=foreign)
    clean = await kitem(session, acc, guidance="Пиши коротко и по делу")
    leaky = await kitem(
        session, acc, guidance="Помни: ставка на длинную дистанцию всегда побеждает быструю"
    )
    await evidence(session, acc, leaky, source_id=src, observed_at=NOW)
    pk = await build(session, acc)
    assert clean in ids(pk)
    assert leaky not in ids(pk)
    assert "ставка на длинную дистанцию" not in to_markdown(pk)


async def test_empty_base_returns_empty_pack_with_warning(session: AsyncSession) -> None:
    acc = await new_account(session)
    pk = await build(session, acc)
    assert pk.items == []
    assert "empty_base" in pk.warnings
    assert re.match(r"^## Знания под задачу", to_markdown(pk))
    n = (
        await session.execute(
            text("SELECT count(*) FROM knowledge_packs WHERE account_id = :a"), {"a": acc}
        )
    ).scalar_one()
    assert n == 1


async def test_search_and_get_item(session: AsyncSession) -> None:
    acc = await new_account(session)
    near = await kitem(
        session, acc, kind="thesis", element="device", scopes={"lang": ["ru"]}, quality=0.7
    )
    far = await kitem(session, acc, vec=axis(5))
    await kitem(session, acc, status="candidate")
    hits = await api.search(session, acc, "как писать про провалы", embedder=embedder(), k=5)
    assert [h.item_id for h in hits][:2] == [near, far]
    only_hooks = await api.search(
        session, acc, "как писать про провалы", embedder=embedder(), filters={"element": "hook"}
    )
    assert [h.item_id for h in only_hooks] == [far]
    card = await api.get_item(session, acc, near)
    assert card.item_id == near and card.scopes == {"lang": ["ru"]}
    assert card.confidence == "high"


async def test_export_pack_formats(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(3):
        await kitem(session, acc)
    md = await export_pack(session, acc, task(), "md", embedder=embedder(), clock=FakeClock(NOW))
    js = await export_pack(session, acc, task(), "json", embedder=embedder(), clock=FakeClock(NOW))
    assert md.decode().startswith("## Знания под задачу")
    assert KnowledgePack.model_validate_json(js).items
    assert to_json(KnowledgePack.model_validate_json(js)) == js.decode()


async def test_export_full_roundtrip_jsonl(session: AsyncSession) -> None:
    acc = await new_account(session)
    a = await kitem(session, acc, scopes={"format": ["post", "reply"], "lang": ["ru"]})
    b = await kitem(session, acc, kind="anti_pattern", element="general", status="probation")
    await kitem(session, acc, status="merged", merged_into=a)
    lines = [ln async for ln in export_full(session, acc)]
    cards = [ItemCard.model_validate_json(ln) for ln in lines]
    assert all(ln.endswith("\n") is False for ln in lines)
    assert {c.item_id for c in cards} == {a, b}
    for c in cards:
        assert c == await api.get_item(session, acc, c.item_id)
    assert next(c for c in cards if c.item_id == a).scopes == {
        "format": ["post", "reply"],
        "lang": ["ru"],
    }


async def test_views_return_expected_rows(session: AsyncSession) -> None:
    acc = await new_account(session)
    high = await kitem(session, acc, quality=0.65, scopes={"format": ["post"]})
    low = await kitem(session, acc, quality=0.3)
    other = await kitem(session, acc, status="active", scopes={"format": ["thread"]})
    await KnowledgeLinksRepo(session, acc).add(
        from_item_id=low,
        to_item_id=other,
        relation="contradicts",
        confidence=0.9,
        decided_by="judge",
    )
    cards = {
        r[0]: r[1:]
        for r in (
            await session.execute(
                text(
                    "SELECT id, confidence_label, open_conflicts, scopes FROM v_knowledge_cards"
                    " WHERE account_id = :a"
                ),
                {"a": acc},
            )
        ).all()
    }
    assert cards[high] == ("high", 0, {"format": ["post"]})
    assert cards[low][:2] == ("low", 1)
    usable = {
        r[0]
        for r in (
            await session.execute(
                text("SELECT id FROM v_knowledge_usable WHERE account_id = :a"), {"a": acc}
            )
        ).all()
    }
    assert usable == {high}
    ctx = make_ctx(session, acc)
    await snapshots.take(ctx, NOW.date())
    on = (
        await session.execute(
            text("SELECT item_id FROM knowledge_state_on(:a, :d)"),
            {"a": acc, "d": NOW.date() + timedelta(days=3)},
        )
    ).all()
    assert {r[0] for r in on} == {high, low, other}
