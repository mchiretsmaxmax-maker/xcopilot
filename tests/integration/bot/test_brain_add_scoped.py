"""fix/brain-add-scoped: «➕ Добавить правило» в разделе формата «Мозга» — область format."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.integration.bot.conftest import Client, count, make_onboarded
from tests.pack_stand import build, task
from xcopilot.bot.deps import BotDeps
from xcopilot.brain.jobs import BrainDeps

SF = async_sessionmaker[AsyncSession]
RULE = "В ответах сначала суть, без вступлений"


@pytest.fixture(autouse=True)
def _brain_deps(deps: BotDeps, clock: FakeClock) -> None:
    deps.brain = BrainDeps(uow_factory=deps.uow_factory, clock=clock, embedder=FakeEmbedder())


async def _owner_items(sf: SF, acc: UUID) -> list[UUID]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT id FROM knowledge_items WHERE account_id = :a AND origin = 'owner'"),
            {"a": acc},
        )
        return [r.id for r in rows]


async def _formats(sf: SF, item: UUID) -> list[str]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT value FROM knowledge_item_scopes WHERE item_id = :i AND facet = 'format'"),
            {"i": item},
        )
        return sorted(r.value for r in rows)


async def _open_replies(owner: Client) -> None:
    await owner.say("/brain")
    await owner.press_data("bn:seg:replies:1")
    assert "➕ Добавить правило" in owner.last.buttons


async def test_add_rule_from_replies_segment_scopes_to_reply(
    owner: Client, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    await _open_replies(owner)
    await owner.press("➕ Добавить правило")
    assert await owner.state() == "BrainFlow:add"
    assert "✖️ Отменить" in owner.last.buttons
    sent = await owner.say(RULE)
    assert await owner.state() is None
    assert any("Добавил в «💬 Ответы»" in m.text and "только в ответах" in m.text for m in sent)
    assert "Ответы" in sent[-1].text  # обновлённый экран сегмента
    [item] = await _owner_items(session_factory, acc)
    assert await _formats(session_factory, item) == ["reply"]
    async with session_factory() as s:
        reply_pack = await build(s, acc, task("reply", budget_tokens=4000))
        post_pack = await build(s, acc, task(budget_tokens=4000))
    assert item in {i.item_id for i in reply_pack.items}
    assert item not in {i.item_id for i in post_pack.items}


async def test_add_rule_cancel_adds_nothing(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    await _open_replies(owner)
    await owner.press("➕ Добавить правило")
    await owner.press("✖️ Отменить")
    assert await owner.state() is None
    await owner.say(RULE)
    assert await count(session_factory, "knowledge_items", f"account_id = '{acc}'") == 0


async def test_add_rule_too_short_keeps_step(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    await _open_replies(owner)
    await owner.press("➕ Добавить правило")
    sent = await owner.say("коротко")
    assert "от 15 до 240" in sent[-1].text
    assert await owner.state() == "BrainFlow:add"
    await owner.say(RULE)
    assert len(await _owner_items(session_factory, acc)) == 1


async def test_card_shows_format_and_where_button_changes_scope(
    owner: Client, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    await _open_replies(owner)
    await owner.press("➕ Добавить правило")
    await owner.say(RULE)
    [item] = await _owner_items(session_factory, acc)
    await owner.press_data(f"bn:item:{item}")
    assert "Где работает: ответы" in owner.last.text
    await owner.press("🎯 Где работает")
    await owner.press("Только посты")
    assert await _formats(session_factory, item) == ["post"]
    await owner.press_data(f"bn:item:{item}")
    assert "Где работает: посты" in owner.last.text
    await owner.press("🎯 Где работает")
    await owner.press("Везде")
    assert await _formats(session_factory, item) == []


async def test_brain_add_command_still_everywhere(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    await owner.say(f"/brain add {RULE}")
    [item] = await _owner_items(session_factory, acc)
    assert await _formats(session_factory, item) == []
