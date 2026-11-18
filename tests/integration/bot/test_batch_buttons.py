"""Кнопки сводки пачки и «Поправить оценку» (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import material
from tests.integration.bot.conftest import Client, count, make_onboarded
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.uow import UnitOfWork

SF = async_sessionmaker[AsyncSession]


async def _batch(sf: SF, acc: UUID, tiers: list[str | None]) -> tuple[str, list[UUID]]:
    batch = str(uuid4())
    ids = []
    async with UnitOfWork(sf) as uow:
        for i, tier in enumerate(tiers):
            sid = await material(
                uow.session,
                acc,
                text=f"пост номер {i}\nвторая строка {i}",
                tier=tier,
                signal_origin="public_metrics",
                outcome_basis="rates",
            )
            await SourcesRepo(uow.session, acc).merge_meta(sid, batch=batch)
            ids.append(sid)
    return batch, ids


async def test_show_cards_paginated_by_five(session_factory: SF, owner: Client) -> None:
    acc = await make_onboarded(session_factory)
    batch, _ = await _batch(session_factory, acc, ["hit"] * 6 + [None])
    replies = await owner.press_data(f"mbs:{batch}:show:0")
    cards = [r for r in replies if r.text.startswith("🧩 Разбор")]
    assert len(cards) == 5
    assert all("Как он зашёл?" not in c.text for c in cards)  # исход уже есть
    assert f"mbs:{batch}:show:5" in replies[-1].callbacks
    more = await owner.press_data(f"mbs:{batch}:show:5")
    cards = [r for r in more if r.text.startswith("🧩 Разбор")]
    assert len(cards) == 2
    assert "Как он зашёл?" in cards[-1].text  # у последнего исхода нет — вопрос остаётся
    assert not any(c.startswith("mbs:") for r in more for c in r.callbacks)


async def test_rate_without_numbers_one_by_one(session_factory: SF, owner: Client) -> None:
    acc = await make_onboarded(session_factory)
    batch, ids = await _batch(session_factory, acc, ["hit", None, None])
    replies = await owner.press_data(f"mbs:{batch}:rate")
    cards = [r for r in replies if r.text.startswith("🧩 Разбор")]
    assert len(cards) == 1
    assert "Как он зашёл?" in cards[0].text
    assert f"mo:{ids[1]}:hit" in cards[0].callbacks
    after = await owner.press_data(f"mo:{ids[1]}:hit")
    assert after[0].text == "Записал: залетел."
    nxt = [r for r in after if r.text.startswith("🧩 Разбор")]
    assert len(nxt) == 1 and f"mo:{ids[2]}:flop" in nxt[0].callbacks
    last = await owner.press_data(f"mo:{ids[2]}:flop")
    assert last[0].text == "Записал: не зашёл."
    assert "Все материалы пачки оценены." in [r.text for r in last]


async def test_mass_stance_changes_batch_and_revises(session_factory: SF, owner: Client) -> None:
    acc = await make_onboarded(session_factory)
    batch, _ = await _batch(session_factory, acc, ["hit", "flop", None])
    replies = await owner.press_data(f"mbs:{batch}:own")
    assert replies[-1].text == "Записал всю пачку (3) как твои посты."
    assert await count(session_factory, "sources", "stance = 'own'") == 3
    assert await count(session_factory, "jobs", "kind = 'brain.detect'") == 3
    replies = await owner.press_data(f"mbs:{batch}:inspiration")
    assert replies[-1].text == "Записал всю пачку (3) как вдохновение."
    assert await count(session_factory, "sources", "stance = 'inspiration'") == 3


async def test_fix_auto_outcome_shows_outcome_buttons(session_factory: SF, owner: Client) -> None:
    acc = await make_onboarded(session_factory)
    _, ids = await _batch(session_factory, acc, ["hit"])
    replies = await owner.press_data(f"moe:{ids[0]}")
    assert replies[-1].text == "Как он зашёл на самом деле?"
    assert f"mo:{ids[0]}:hit" in replies[-1].callbacks
    assert f"mo:{ids[0]}:flop" in replies[-1].callbacks
