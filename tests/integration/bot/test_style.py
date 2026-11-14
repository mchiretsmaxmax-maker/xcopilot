"""E2E: «🎨 Стиль» — паспорт, правила, версии, откат и экспорт через диспетчер бота."""

from __future__ import annotations

from uuid import UUID

import pytest
from aiogram import Dispatcher
from aiogram.methods import SendDocument
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.llm import FakeLLM
from tests.integration.bot.conftest import OWNER, Client, make_onboarded
from tests.style_stand import LOWER_POSTS, make_services, model_reply, seed_chunks
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.style.passport import Example, HardRules, StylePassport
from xcopilot.style.service import StyleService

SF = async_sessionmaker[AsyncSession]


class Rig:
    def __init__(self, service: StyleService, owner: Client, acc: UUID, writer: FakeLLM) -> None:
        self.service, self.owner, self.acc, self.writer = service, owner, acc, writer


@pytest.fixture
async def rig(session_factory: SF, clock: FakeClock, telegram: FakeTelegram, deps: BotDeps) -> Rig:
    acc = await make_onboarded(session_factory)
    writer = FakeLLM(name="anthropic")
    service, _ = make_services(session_factory, clock, writer)
    deps.style = service
    dp: Dispatcher = build_dispatcher(deps)
    return Rig(service, Client(dp, telegram, OWNER), acc, writer)


async def statuses(sf: SF, acc: UUID) -> dict[int, str]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT version, status FROM style_passports WHERE account_id=:a"), {"a": acc}
        )
        return {r[0]: r[1] for r in rows}


async def actions(sf: SF) -> list[str]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT props->>'action' FROM events WHERE type='user.action' ORDER BY ts, id")
        )
        return [r[0] for r in rows]


def base() -> StylePassport:
    return StylePassport(
        summary="спокойный технарь",
        tone="спокойный, точный",
        hard_rules=HardRules(casing="lower", emoji="none", hashtags="none"),
        examples=[Example(chunk_id="c1", text="блобы сделали l2 дешевле")],
    )


async def test_style_menu_without_passport_offers_rebuild(rig: Rig) -> None:
    await rig.owner.press_data("menu:style")
    assert "паспорта пока нет" in rig.owner.last.text.lower()
    assert "Пересобрать" in rig.owner.last.buttons


async def test_style_menu_shows_active_passport(rig: Rig) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("menu:style")
    shown = rig.owner.last
    assert shown.text.startswith("Паспорт v1 (активный). Тон: спокойный, точный.")
    assert "строчные буквы, без эмодзи, без хэштегов, до 280 знаков" in shown.text
    for label in ["Правила", "Примеры", "История версий", "Пересобрать", "Скачать .md"]:
        assert label in shown.buttons


async def test_rebuild_then_activate(rig: Rig, session_factory: SF) -> None:
    await seed_chunks(session_factory, rig.acc, LOWER_POSTS)
    rig.writer.push(model_reply())
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Пересобрать")
    assert rig.owner.last.text.startswith("Собрал паспорт v1.")
    assert "Активировать" in rig.owner.last.buttons
    await rig.owner.press("Активировать")
    assert await statuses(session_factory, rig.acc) == {1: "active"}
    assert "v1" in rig.owner.last.text and "активен" in rig.owner.last.text


async def test_rebuild_keep_old_leaves_draft(rig: Rig, session_factory: SF) -> None:
    await seed_chunks(session_factory, rig.acc, LOWER_POSTS)
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    rig.writer.push(model_reply(summary="новое"))
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Пересобрать")
    assert rig.owner.last.text.startswith("Собрал паспорт v2. Изменилось:")
    assert "Оставить v1" in rig.owner.last.buttons
    await rig.owner.press("Оставить v1")
    assert await statuses(session_factory, rig.acc) == {1: "active", 2: "draft"}


async def test_rebuild_without_posts_explains(rig: Rig) -> None:
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Пересобрать")
    assert "постов" in rig.owner.last.text


async def test_rollback_button_from_bot(rig: Rig, session_factory: SF) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.service.activate(rig.acc, 2)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("История версий")
    assert "v2" in rig.owner.last.text and "v1" in rig.owner.last.text
    assert "Откатить к v1" in rig.owner.last.buttons
    await rig.owner.press("Откатить к v1")
    assert await statuses(session_factory, rig.acc) == {1: "active", 2: "archived"}
    assert "v1" in rig.owner.last.text


async def test_edit_rules_by_text_and_toggle(rig: Rig, session_factory: SF) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.say("добавь запрет: Delve")
    assert "v2" in rig.owner.last.text and "Активировать" in rig.owner.last.buttons
    await rig.owner.say("добавь запрет: delve")
    assert "уже" in rig.owner.last.text
    await rig.owner.press("Активировать")
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Правила")
    assert "1." in rig.owner.last.text and "delve" in rig.owner.last.text.lower()
    await rig.owner.press_data("st:tg:links")  # ссылки: свободно → нет
    stored = await rig.service.get(rig.acc, 3)
    assert stored is not None and stored.passport.hard_rules.links == "none"
    assert stored.created_by == "user"
    await rig.owner.say("убери правило 99")
    assert "нет правила" in rig.owner.last.text.lower()
    await rig.owner.say("убери правило 1")
    latest = await rig.service.get(rig.acc, 4)
    assert latest is not None and latest.passport.hard_rules.casing == "any"
    assert await statuses(session_factory, rig.acc) == {
        1: "archived",
        2: "active",
        3: "draft",
        4: "draft",
    }


async def test_examples_view(rig: Rig) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Примеры")
    assert "блобы сделали l2 дешевле" in rig.owner.last.text


async def test_download_md_sends_document(rig: Rig, telegram: FakeTelegram) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("Скачать .md")
    docs = [c for c in telegram.calls if isinstance(c, SendDocument)]
    assert len(docs) == 1
    file = docs[0].document
    assert getattr(file, "filename", "").endswith(".md")
    assert "## Тон" in bytes(getattr(file, "data", b"")).decode()


async def test_style_actions_are_recorded(rig: Rig, session_factory: SF) -> None:
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("История версий")
    await rig.owner.say("добавь запрет: delve")
    seen = await actions(session_factory)
    assert {"menu.style", "style.history", "style.ban"} <= set(seen)


async def test_style_unavailable_without_service(rig: Rig, deps: BotDeps) -> None:
    deps.style = None
    await rig.owner.press_data("menu:style")
    assert "недоступен" in rig.owner.last.text.lower()
