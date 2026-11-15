"""Кнопки карточки разбора через диспетчер (этап 7A, задача 8) — дополнительные тесты."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.bot.conftest import Client, count, make_onboarded
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.uow import UnitOfWork

SF = async_sessionmaker[AsyncSession]


async def _material(sf: SF, acc: UUID, clock: FakeClock) -> UUID:
    async with UnitOfWork(sf) as uow:
        repo = SourcesRepo(uow.session, acc)
        src, _ = await repo.add_or_get(
            kind="post",
            stance="inspiration",
            origin="manual_text",
            content_hash="card-test",
            posted_at=clock.now() - timedelta(days=3),
            status="indexed",
        )
        await repo.add_document(src.id, text="i lost $40k\nshort", text_clean="i lost $40k\nshort")
        return src.id


async def test_outcome_buttons_write_label_and_outcome(
    session_factory: SF, clock: FakeClock, owner: Client
) -> None:
    acc = await make_onboarded(session_factory)
    sid = await _material(session_factory, acc, clock)
    replies = await owner.press_data(f"mo:{sid}:hit")
    assert replies[-1].text == "Записал: залетел."
    assert await count(session_factory, "material_signals", "origin = 'owner_label'") == 1
    where = f"source_id = '{sid}' AND is_current AND basis = 'owner_label'"
    assert await count(session_factory, "material_outcomes", where) == 1
    await owner.press_data(f"mo:{sid}:bogus")  # чужое значение — ничего не пишем
    await owner.press_data("mo:not-a-uuid:hit")
    assert await count(session_factory, "material_signals") == 1


async def test_numbers_button_waits_for_text(
    session_factory: SF, clock: FakeClock, owner: Client
) -> None:
    acc = await make_onboarded(session_factory)
    sid = await _material(session_factory, acc, clock)
    await owner.press_data(f"mo:{sid}:nums")
    assert "одной строкой" in owner.last.text
    await owner.say("ничего")
    assert owner.last.text.startswith("Не нашёл цифр")
    await owner.say("12.4K views, 340 likes")
    assert owner.last.text == "Записал цифры: 12400 просмотров, 340 лайков."
    assert await owner.state() is None
    assert await count(session_factory, "material_signals", "origin = 'manual_numbers'") == 1
    assert (
        await count(
            session_factory, "material_outcomes", f"source_id = '{sid}' AND basis = 'rates'"
        )
        == 1
    )


async def test_stance_buttons_change_relation(
    session_factory: SF, clock: FakeClock, owner: Client
) -> None:
    acc = await make_onboarded(session_factory)
    sid = await _material(session_factory, acc, clock)
    await owner.press_data(f"ms:{sid}:anti_example")
    assert owner.last.text == "Записал как антипример."
    assert await count(session_factory, "sources", f"id = '{sid}' AND stance = 'anti_example'") == 1
    await owner.press_data(f"ms:{sid}:competitor")  # не из карточки — игнор
    assert await count(session_factory, "sources", f"id = '{sid}' AND stance = 'anti_example'") == 1
