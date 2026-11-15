"""Этап 9C: «Мозг» в Telegram — меню, карточки, действия, класс A, откат, шаг онбординга."""

from __future__ import annotations

import re
from uuid import UUID

import pytest
from aiogram import Dispatcher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import knowledge, make_ctx, material, new_author
from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.embedder import FakeEmbedder
from tests.integration.bot.conftest import (
    EDITOR,
    OWNER,
    Client,
    count,
    make_onboarded,
)
from xcopilot.bot.deps import BotDeps
from xcopilot.brain import lifecycle
from xcopilot.brain.evidence import record_from_material
from xcopilot.brain.jobs import BrainDeps
from xcopilot.db.uow import UnitOfWork

SF = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def _brain_deps(deps: BotDeps, clock: FakeClock) -> None:
    deps.brain = BrainDeps(uow_factory=deps.uow_factory, clock=clock, embedder=FakeEmbedder())


async def seed(sf: SF, acc: UUID, **kw: object) -> UUID:
    async with UnitOfWork(sf) as uow:
        return await knowledge(uow.session, acc, **kw)  # type: ignore[arg-type]


async def status_of(sf: SF, item: UUID) -> tuple[str, str]:
    async with sf() as s:
        row = (
            await s.execute(
                text("SELECT status, owner_state FROM knowledge_items WHERE id = :i"), {"i": item}
            )
        ).one()
        return row.status, row.owner_state


# ---------------------------------------------------------------- /brain


async def test_brain_menu_shows_counts_by_status_and_segment(
    owner: Client, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    for n in range(2):
        await seed(
            session_factory, acc, statement=f"Рабочее правило номер {n} про хук", status="active"
        )
    await seed(session_factory, acc, statement="Испытуемое правило про хук", status="probation")
    await seed(
        session_factory,
        acc,
        statement="Кандидат для тредов про хук",
        scopes={"format": ["thread"]},
    )
    await seed(
        session_factory,
        acc,
        statement="Ты не используешь восклицательные знаки",
        layer="voice",
        element="general",
    )
    async with UnitOfWork(session_factory) as uow:
        for _ in range(3):
            await material(uow.session, acc)

    sent = await owner.say("/brain")
    menu = sent[0]
    assert re.search(r"🧠 Мозг · 3 материала? · 5 знани", menu.text)
    assert "В работе 2 · на испытании 1 · кандидатов 2" in menu.text
    for label in ("🎙 Голос 1", "🪝 Хуки 4", "📝 Посты 3", "🧵 Треды 4", "⚠️ Конфликты 0"):
        assert label in menu.buttons, menu.buttons
    for label in ("🎯 По цели", "🆕 За неделю", "🔎 Поиск", "📤 Экспорт", "⏪ Откат"):
        assert any(b.startswith(label) for b in menu.buttons), label
    assert await count(session_factory, "segments", f"account_id = '{acc}'") >= 13


async def test_item_card_shows_words_and_numbers(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(
        session_factory,
        acc,
        statement="Личная цифра в первой строке даёт больше ответов",
        guidance="Начни с конкретной цифры из своего опыта",
        status="probation",
        scopes={"format": ["post", "thread"], "goal": ["replies"]},
    )
    async with UnitOfWork(session_factory) as uow:
        ctx = make_ctx(uow.session, acc)
        for _ in range(2):
            sid = await material(uow.session, acc, author_id=await new_author(uow.session, acc))
            await record_from_material(ctx, sid)
        await uow.session.execute(
            text("UPDATE knowledge_items SET quality = 0.57 WHERE id = :i"), {"i": item}
        )
    await owner.say("/brain")
    listing = await owner.press("🪝 Хуки 1")
    assert "Хуки" in listing[-1].text
    assert any("Личная цифра" in b for b in listing[-1].buttons)
    card = (await owner.press(next(b for b in listing[-1].buttons if "Личная цифра" in b)))[-1]
    assert "«Личная цифра в первой строке даёт больше ответов»" in card.text
    assert "Как применять: Начни с конкретной цифры из своего опыта" in card.text
    assert "Уверенность: средняя (0.57)" in card.text
    assert "Подходит тебе: пока мало данных" in card.text
    assert "Доказательства: 2 поста от 2 авторов" in card.text
    assert "Где работает: посты, треды · цель: ответы" in card.text
    for label in (
        "📎 Доказательства",
        "✏️ Изменить",
        "📌 Закрепить",
        "✅ В работу",
        "🚫 Не моё",
        "🗑 Удалить",
    ):
        assert label in card.buttons, card.buttons
    evidence = (await owner.press("📎 Доказательства"))[-1]
    assert "Доказательства" in evidence.text and any(b.startswith("🗑") for b in evidence.buttons)


async def test_card_actions_confirm_pin_not_mine_edit(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(
        session_factory, acc, statement="Короткие строки читаются быстрее", status="probation"
    )
    await owner.say("/brain")
    listing = await owner.press("🪝 Хуки 1")
    await owner.press(next(b for b in listing[-1].buttons if "Короткие строки" in b))
    await owner.press("✅ В работу")
    assert await status_of(session_factory, item) == ("active", "confirmed")
    await owner.press("📌 Закрепить")
    assert await status_of(session_factory, item) == ("active", "pinned")
    await owner.press("🚫 Не моё")
    assert await status_of(session_factory, item) == ("active", "disabled")
    await owner.press("✏️ Изменить")
    assert await owner.state() == "BrainFlow:edit"
    await owner.say("Короткие строки читаются быстрее длинных абзацев")
    assert await owner.state() is None
    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT statement, version FROM knowledge_items WHERE id = :i"), {"i": item}
            )
        ).one()
    assert row.version == 2 and row.statement.endswith("длинных абзацев")


async def test_delete_asks_confirmation(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(
        session_factory, acc, statement="Концовка-вопрос звучит как AI", status="active"
    )
    await owner.say("/brain")
    listing = await owner.press("🪝 Хуки 1")
    await owner.press(next(b for b in listing[-1].buttons if "Концовка" in b))
    ask = (await owner.press("🗑 Удалить"))[-1]
    assert "Удалить знание и не предлагать похожие 30 дней?" in ask.text
    assert ask.buttons == ["Да", "Нет"]
    assert await status_of(session_factory, item) == ("active", "none")
    await owner.press("Нет")
    assert await status_of(session_factory, item) == ("active", "none")
    await owner.press("🗑 Удалить")
    await owner.press("Да")
    assert (await status_of(session_factory, item))[0] == "rejected"


async def test_class_a_candidate_requires_explicit_yes(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(
        session_factory,
        acc,
        statement="Ты убираешь let's в начале поста",
        guidance="Не начинай пост с let's",
        layer="voice",
        element="general",
    )
    sent = await owner.say("/brain")
    prompt = next(m for m in sent if m.text.startswith("🎙"))
    assert prompt.buttons == ["Да", "Нет", "Изменить"]
    assert "let's" in prompt.text
    async with UnitOfWork(session_factory) as uow:  # автоматика класс A не повышает
        await lifecycle.run(make_ctx(uow.session, acc))
    assert await status_of(session_factory, item) == ("candidate", "none")
    await owner.press("Да")
    assert await status_of(session_factory, item) == ("active", "confirmed")


async def test_class_a_no_rejects(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(
        session_factory,
        acc,
        statement="Ты пишешь заглавными буквами",
        layer="voice",
        element="general",
    )
    await owner.say("/brain")
    await owner.press("Нет")
    assert (await status_of(session_factory, item))[0] == "rejected"


async def test_conflict_buttons_in_brain(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    first = await seed(
        session_factory, acc, statement="Вопрос в конце поднимает ответы", status="active"
    )
    second = await seed(
        session_factory, acc, statement="Концовка-вопрос звучит как AI", status="active"
    )
    from xcopilot.brain.merge import Near, Relation, apply_relation

    async with UnitOfWork(session_factory) as uow:
        await apply_relation(
            make_ctx(uow.session, acc), second, Near(first, "x", 0.9), Relation("contradicts", 0.9)
        )
    menu = (await owner.say("/brain"))[0]
    assert "⚠️ Конфликты 1" in menu.buttons
    view = (await owner.press("⚠️ Конфликты 1"))[-1]
    assert {"Первое", "Второе"} <= set(view.buttons)
    assert any("развести" in b.lower() for b in view.buttons)
    await owner.press("Первое")
    assert await status_of(session_factory, second) == ("active", "none")
    assert (await status_of(session_factory, first))[0] == "archived"


async def test_add_and_search_commands(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    sent = await owner.say("/brain add Не начинай пост с извинений и вступлений")
    assert "Добавил" in sent[-1].text
    assert (
        await count(
            session_factory, "knowledge_items", f"account_id = '{acc}' AND origin = 'owner'"
        )
        == 1
    )
    found = await owner.say("/brain search извинений")
    assert "извинений" in found[-1].text
    none = await owner.say("/brain search нетакого")
    assert "Ничего не нашёл" in none[-1].text
    bad = await owner.say("/brain add коротко")
    assert "Не добавил" in bad[-1].text
    assert await count(session_factory, "knowledge_items", f"account_id = '{acc}'") == 1


async def test_non_owner_is_refused_in_bot(
    session_factory: SF, dp: Dispatcher, telegram: FakeTelegram
) -> None:
    acc = await make_onboarded(session_factory)
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    item = await seed(
        session_factory, acc, statement="Короткие строки читаются быстрее", status="probation"
    )
    editor = Client(dp, telegram, EDITOR)
    await editor.say("/brain")  # читать можно
    listing = await editor.press("🪝 Хуки 1")
    await editor.press(next(b for b in listing[-1].buttons if "Короткие строки" in b))
    await editor.press_data(f"bn:ok:{item}")
    assert "только владелец" in editor.last.text.lower()
    assert await status_of(session_factory, item) == ("probation", "none")
    add = await editor.say("/brain add Не начинай пост с извинений и вступлений")
    assert "только владелец" in add[-1].text.lower()


# ---------------------------------------------------------------- откат


async def test_rollback_preview_then_confirm(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    items = [
        await seed(
            session_factory, acc, statement=f"Правило {n} про структуру поста", status="active"
        )
        for n in range(2)
    ]
    preview = (await owner.say("/brain undo 2020-01-01"))[-1]
    assert "Откат на 1 января" in preview.text
    assert re.search(r"2 нов\w+ знани\w+", preview.text)
    assert "Материалы и разборы останутся" in preview.text
    assert preview.buttons == ["Откатить", "Отмена"]
    for item in items:
        assert (await status_of(session_factory, item))[0] == "active"  # до «Откатить» ничего
    await owner.press("Отмена")
    assert (await status_of(session_factory, items[0]))[0] == "active"
    await owner.say("/brain undo 2020-01-01")
    done = (await owner.press("Откатить"))[-1]
    assert "Откатил" in done.text and "Отменить откат" in done.buttons
    for item in items:
        assert (await status_of(session_factory, item))[0] == "archived"
    assert await count(session_factory, "events", "type = 'knowledge.rollback'") == 1
    await owner.press("Отменить откат")
    for item in items:
        assert (await status_of(session_factory, item))[0] == "active"


# ---------------------------------------------------------------- сводка и настройки


async def test_week_command_and_digest_batch_buttons(
    owner: Client, session_factory: SF, clock: FakeClock
) -> None:
    acc = await make_onboarded(session_factory)
    item = await seed(session_factory, acc, statement="Одна мысль на строку, без связок")
    async with UnitOfWork(session_factory) as uow:
        ctx = make_ctx(uow.session, acc, clock=clock)
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(item, "candidate", "probation", "auto_probation", "тест"),
        )
    digest_msg = (await owner.say("/brain week"))[-1]
    assert "Неделя Мозга" in digest_msg.text
    assert "Принять всё" in digest_msg.buttons and "Разобрать по одному" in digest_msg.buttons
    await owner.press("Принять всё")
    assert await status_of(session_factory, item) == ("active", "confirmed")


async def test_settings_toggle_autonomy(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    shown = (await owner.say("/brain settings"))[-1]
    assert "Автоиспытания: включены" in shown.text
    await owner.press("Выключить автоиспытания")
    async with session_factory() as s:
        settings = (
            await s.execute(text("SELECT settings FROM accounts WHERE id = :a"), {"a": acc})
        ).scalar_one()
    assert settings["brain"]["autonomy"] == "manual"
    assert "выключены" in owner.last.text


# ---------------------------------------------------------------- онбординг: шаг «что работает»


async def _confirm_onboarding(c: Client) -> list:  # type: ignore[type-arg]
    await c.say("/start")
    await c.press("Крипта")
    await c.press("Русский")
    await c.say("@alice")
    await c.press("Дальше")
    await c.press("Да")
    await c.press("Пропустить")
    return await c.press("Всё верно")


async def test_onboarding_rules_step_creates_owner_items_and_is_skippable(
    owner: Client, session_factory: SF
) -> None:
    sent = await _confirm_onboarding(owner)
    prompt = next(m for m in sent if "Что у тебя точно работает" in m.text)
    assert "Написать правила" in prompt.buttons and "Пропустить" in prompt.buttons
    assert (
        await count(
            session_factory,
            "segments",
            "account_id = (SELECT id FROM accounts WHERE handle = 'alice')",
        )
        >= 13
    )
    await owner.press("Написать правила")
    assert await owner.state() == "Onboarding:rules"
    reply = await owner.say(
        "Не начинай пост с извинений\n"
        "Пиши короткими строками, без воды\n"
        "Каждый пост начинай с конкретной цифры"
    )
    assert "Добавил 3" in reply[-1].text
    assert (
        await count(
            session_factory,
            "knowledge_items",
            "origin = 'owner' AND status = 'active' AND owner_state = 'confirmed'",
        )
        == 3
    )
    await owner.press("Готово")
    assert await owner.state() is None


async def test_onboarding_rules_step_skip_creates_nothing(
    owner: Client, session_factory: SF
) -> None:
    await _confirm_onboarding(owner)
    await owner.press("Пропустить")
    assert await owner.state() is None
    assert await count(session_factory, "knowledge_items", "origin = 'owner'") == 0
    assert OWNER  # онбординг завершён и без правил: бот отвечает меню
    sent = await owner.say("/brain")
    assert "🧠 Мозг" in sent[0].text
