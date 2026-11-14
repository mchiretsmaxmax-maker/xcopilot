"""E2E ТЗ v3.1: скрины, пересылки и кнопки (отношение, «Убрать», цифры) через бота; пачки."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.screens import counter, png, x_read
from tests.fakes.telegram import forwarded_update, image_document_update, photo_update
from tests.ingest_stand import Stand, make_stand, post
from tests.integration.bot.conftest import OWNER, Client, count
from tests.samples import unique_text
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.bot.routers.learn import build_ingest_debouncer

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
async def learn(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram, deps: BotDeps
) -> tuple[Stand, Client]:
    stand = await make_stand(
        session_factory, clock, notifier=Notifier(telegram.bot, deps.uow_factory), handle="acc"
    )
    deps.ingest = stand.service
    dp: Dispatcher = build_dispatcher(deps)
    return stand, Client(dp, telegram, OWNER)


async def test_photo_becomes_screenshot_and_counter_buttons_work(
    learn: tuple[Stand, Client], telegram: FakeTelegram, session_factory: SF
) -> None:
    stand, owner = learn
    image = png(500)
    telegram.files["p7001"] = image
    stand.screenshots.by_image[image] = x_read(
        "Hook first, details later", bookmarks=counter(3_400, "3.4K", confidence=0.3)
    )
    replies = await owner.feed(photo_update(7001, OWNER, caption="хук"))
    assert replies[-1].text.startswith("Принял 1 материал: 1 скрин.")
    await stand.run_jobs()
    assert stand.screenshots.calls == [(image, "хук")]
    await owner.press("34K")
    assert owner.last.text == "Записал: 34 тыс. — закладок."
    assert await count(session_factory, "material_signals", "bookmarks = 34000") == 1
    await owner.press("Не важно")
    assert owner.last.text == "Хорошо, без этой цифры."
    await owner.press("Факты")
    assert owner.last.text == "Запомнил: Факты."
    assert await count(session_factory, "sources", "stance = 'reference'") == 1
    await owner.press("🗑 Убрать")
    assert owner.last.text.startswith("Убрал: 1 материал.")
    await owner.press("🗑 Убрать")
    assert owner.last.text == "Уже убрано."


async def test_not_a_post_buttons(learn: tuple[Stand, Client], telegram: FakeTelegram) -> None:
    stand, owner = learn
    first, second = png(510), png(511)
    telegram.files["f7101"] = first
    telegram.files["f7102"] = second
    for image in (first, second):
        stand.screenshots.by_image[image] = x_read(
            unique_text(int.from_bytes(image[-2:], "big")), is_x_post=False, kind="other"
        )
    await owner.feed(image_document_update(7101, OWNER))
    await owner.feed(image_document_update(7102, OWNER, mime="image/jpeg"))
    await stand.run_jobs()
    await owner.press("Разобрать как текст")
    assert owner.last.text == "Хорошо, разберу как текст."
    await owner.press("Разобрать как текст")  # первая кнопка в истории уже отработала
    assert owner.last.text in ("Хорошо, разберу как текст.", "Этот скрин уже разобран или убран.")
    await owner.press("Убрать")
    assert owner.last.text.startswith(("Убрал", "Уже"))


async def test_forwarded_messages_keep_author(
    learn: tuple[Stand, Client], session_factory: SF
) -> None:
    stand, owner = learn
    origins: list[dict[str, Any]] = [
        {
            "type": "user",
            "sender_user": {"id": 5, "is_bot": False, "first_name": "Ann", "username": "ann_tg"},
        },
        {"type": "hidden_user", "sender_user_name": "Скрытый"},
        {
            "type": "channel",
            "chat": {"id": -100, "type": "channel", "title": "Канал", "username": "chan"},
            "message_id": 1,
        },
        {"type": "chat", "sender_chat": {"id": -200, "type": "supergroup", "title": "Группа"}},
    ]
    for i, origin in enumerate(origins):
        await owner.feed(forwarded_update(7200 + i, OWNER, unique_text(7200 + i), origin))
    await stand.run_jobs()
    assert await count(session_factory, "sources", "origin = 'tg_forward'") == 4
    rows = await stand.rows(
        "SELECT meta->'forward_from'->>'name' FROM sources ORDER BY created_at, id"
    )
    assert [r[0] for r in rows] == ["Ann", "Скрытый", "Канал", "Группа"]


async def test_legacy_kind_button_still_works(
    learn: tuple[Stand, Client], session_factory: SF
) -> None:
    stand, owner = learn
    receipt = await stand.accept(unique_text(7300))
    await stand.run_jobs()
    await owner.press_data(f"ikind:{receipt.batch_id}:note")
    assert owner.last.text == "Запомнил: Материал и факты."
    await owner.press_data("irel:not-a-uuid:own")
    await owner.press_data("iscr:bad")
    await owner.press_data("irm:bad")


async def test_series_of_messages_makes_one_batch(
    learn: tuple[Stand, Client], deps: BotDeps, telegram: FakeTelegram
) -> None:
    stand, owner = learn
    gate = asyncio.Event()

    async def gated_sleep(_: float) -> None:
        await gate.wait()  # окно «закрывает» тест, а не часы: без гонок под нагрузкой

    deps.ingest_batch = build_ingest_debouncer(deps, sleep=gated_sleep)
    stand.x.add_post(post("7401", unique_text(7401)))
    image = png(520)
    telegram.files["p7402"] = image
    stand.screenshots.by_image[image] = x_read(unique_text(7402))
    assert await owner.say("https://x.com/alice/status/7401") == []
    assert await owner.feed(photo_update(7402, OWNER)) == []
    gate.set()
    await deps.ingest_batch.drain()
    assert owner.last.text.startswith("Принял 2 материала: 1 ссылка, 1 скрин.")
    await stand.run_jobs()
    assert owner.last.text.startswith("Готово: 2 разобрано, 0 дублей.")
