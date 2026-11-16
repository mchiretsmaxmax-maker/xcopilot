"""«Обучить сейчас» в меню «Мозга»: ставит brain.extract с force."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import material, new_author
from tests.integration.bot.conftest import Client, make_onboarded
from tests.integration.bot.test_brain_bot import _brain_deps  # noqa: F401
from xcopilot.db.uow import UnitOfWork

SF = async_sessionmaker[AsyncSession]


async def _jobs(sf: SF, acc: object) -> list[dict[str, object]]:
    async with sf() as s:
        res = await s.execute(
            text("SELECT payload FROM jobs WHERE account_id=:a AND kind='brain.extract'"),
            {"a": acc},
        )
        return [dict(r._mapping) for r in res.all()]


async def test_menu_has_learn_button(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    menu = (await owner.say("/brain"))[0]
    assert any(b.startswith("🎓 Обучить сейчас") for b in menu.buttons), menu.buttons


async def test_learn_now_enqueues_forced_extract_once(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    async with UnitOfWork(session_factory) as uow:
        for _ in range(3):
            await material(uow.session, acc, author_id=await new_author(uow.session, acc))
    await owner.say("/brain")
    sent = await owner.press("🎓 Обучить сейчас")
    assert any("Учусь на 3 материалах" in m.text for m in sent)
    assert [j["payload"] for j in await _jobs(session_factory, acc)] == [{"force": True}]
    again = await owner.press("🎓 Обучить сейчас")
    assert len(await _jobs(session_factory, acc)) == 1
    assert any("уже" in m.text.lower() for m in again)


async def test_learn_now_without_materials_says_so(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    await owner.say("/brain")
    sent = await owner.press("🎓 Обучить сейчас")
    assert await _jobs(session_factory, acc) == []
    assert any("нет" in m.text.lower() for m in sent)
