# ruff: noqa: F811
"""«🎨 Стиль» как файл: загрузка своего паспорта, экран «🕘 Паспорта», жёсткие правила."""

from __future__ import annotations

import itertools

from aiogram.methods import SendDocument
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.fakes.telegram import document_update
from tests.integration.bot.conftest import EDITOR, Client, make_onboarded
from tests.integration.bot.test_style import Rig, base, rig, statuses  # noqa: F401
from tests.style_stand import LOWER_POSTS, model_reply, seed_chunks
from xcopilot.style.passport import StylePassport
from xcopilot.style.render_md import export_md

SF = async_sessionmaker[AsyncSession]
_ids = itertools.count(70_000)


async def active_base(r: Rig) -> None:
    await r.service.create_draft_version(r.acc, base())
    await r.service.activate(r.acc, 1)


def edited_md() -> str:
    md = export_md(base())
    md = md.replace("эмодзи: нет", "эмодзи: редко")
    return md.replace("# Мои правила\n\n—", "# Мои правила\n\nПиши без клише.")


async def send_file(r: Rig, tg: FakeTelegram, text: str, name: str = "passport.md") -> None:
    uid = next(_ids)
    tg.files[f"f{uid}"] = text.encode()
    await r.owner.feed(document_update(uid, r.owner.user_id, name, len(text.encode())))


async def test_home_is_one_summary_with_two_buttons_per_row(rig: Rig) -> None:
    await active_base(rig)
    await rig.owner.press_data("menu:style")
    home = rig.owner.last
    assert home.text.startswith("Паспорт v1 · активный · от ")
    for label in ("📄 Скачать", "📤 Загрузить свой", "🔁 Пересобрать", "🕘 Паспорта"):
        assert label in home.buttons, label
    assert "⚙️ Жёсткие правила" in home.buttons
    assert all(len(row) <= 2 for row in home.reply_markup.inline_keyboard)


async def test_upload_file_diff_then_apply(
    rig: Rig, telegram: FakeTelegram, session_factory: SF
) -> None:
    await active_base(rig)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("📤 Загрузить свой")
    assert "✖️ Отменить" in rig.owner.last.buttons
    await send_file(rig, telegram, edited_md())
    shown = rig.owner.last
    assert "Мои правила: +1" in shown.text
    assert "эмодзи нет → редко" in shown.text
    assert {"✅ Применить", "✖️ Отменить"} <= set(shown.buttons)
    assert await statuses(session_factory, rig.acc) == {1: "active"}
    await rig.owner.press("✅ Применить")
    assert await statuses(session_factory, rig.acc) == {1: "archived", 2: "active"}
    v2 = await rig.service.get(rig.acc, 2)
    assert v2 is not None and v2.created_by == "user" and v2.passport.origin == "file"
    assert v2.passport.owner_rules == "Пиши без клише."
    assert await rig.owner.state() is None


async def test_upload_as_text_then_cancel(rig: Rig, session_factory: SF) -> None:
    await active_base(rig)
    await rig.owner.press_data("st:up")
    await rig.owner.say(edited_md())
    assert "✅ Применить" in rig.owner.last.buttons
    await rig.owner.press("✖️ Отменить")
    assert await statuses(session_factory, rig.acc) == {1: "active"}
    assert await rig.owner.state() is None


async def test_upload_bad_file_explains_and_waits(rig: Rig, telegram: FakeTelegram) -> None:
    await active_base(rig)
    await rig.owner.press_data("st:up")
    await send_file(rig, telegram, export_md(base()).replace("эмодзи: нет", "эмодзи: иногда"))
    assert "Строка" in rig.owner.last.text and "редко" in rig.owner.last.text
    assert "✖️ Отменить" in rig.owner.last.buttons
    assert await rig.owner.state() is not None  # можно прислать исправленный
    await send_file(rig, telegram, edited_md())
    assert "✅ Применить" in rig.owner.last.buttons


async def test_upload_does_not_go_to_ingest(
    rig: Rig, telegram: FakeTelegram, session_factory: SF
) -> None:
    from tests.integration.bot.conftest import count

    await active_base(rig)
    await rig.owner.press_data("st:up")
    await send_file(rig, telegram, edited_md(), name="STYLE.md")
    await rig.owner.press("✅ Применить")
    assert await count(session_factory, "sources") == 0


async def test_editor_cannot_upload(rig: Rig, telegram: FakeTelegram, session_factory: SF) -> None:
    await active_base(rig)
    await make_onboarded(session_factory, tg_id=EDITOR, handle="acc", role="editor")
    editor = Client(rig.owner.dp, telegram, EDITOR)
    await editor.press_data("st:up")
    assert "владел" in editor.last.text.lower()
    assert await editor.state() is None


async def test_passports_screen_list_pages_name_compare_activate(
    rig: Rig, telegram: FakeTelegram, session_factory: SF
) -> None:
    for i in range(10):
        await rig.service.create_draft_version(rig.acc, StylePassport(tone=f"тон {i}"))
    await rig.service.activate(rig.acc, 10)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("🕘 Паспорта")
    page = rig.owner.last
    versions = [c for c in page.callbacks if c.startswith("st:pv:")]
    assert len(versions) == 8
    assert versions[0] == "st:pv:10"  # новые сверху
    assert any(b.startswith("v10") and "✅" in b for b in page.buttons)
    assert "st:pp:1" in page.callbacks  # следующая страница
    await rig.owner.press_data("st:pp:1")
    assert [c for c in rig.owner.last.callbacks if c.startswith("st:pv:")] == ["st:pv:2", "st:pv:1"]

    await rig.owner.press_data("st:pv:3")
    card = rig.owner.last
    assert "v3" in card.text and "тон 2" in card.text
    for label in (
        "👁 Показать целиком",
        "⚖️ Сравнить с активной",
        "✅ Сделать активной",
        "📄 Скачать",
        "✏️ Назвать",
    ):
        assert label in card.buttons, label

    await rig.owner.press("✏️ Назвать")
    assert "✖️ Отменить" in rig.owner.last.buttons
    await rig.owner.say("для ответов")
    named = await rig.service.get(rig.acc, 3)
    assert named is not None and named.passport.label == "для ответов"
    await rig.owner.press_data("st:pp:0")  # v3 — на первой странице (v10…v3)
    assert any("для ответов" in b for b in rig.owner.last.buttons)

    await rig.owner.press_data("st:pv:3")
    await rig.owner.press("⚖️ Сравнить с активной")
    assert "Тон: изменён" in rig.owner.last.text

    await rig.owner.press_data("st:pv:3")
    await rig.owner.press("📄 Скачать")
    docs = [c for c in telegram.calls if isinstance(c, SendDocument)]
    assert docs and "тон 2" in bytes(getattr(docs[-1].document, "data", b"")).decode()

    await rig.owner.press_data("st:pv:3")
    await rig.owner.press("👁 Показать целиком")
    assert "# Тон" in rig.owner.last.text or "тон 2" in rig.owner.last.text

    await rig.owner.press_data("st:pv:3")
    await rig.owner.press("✅ Сделать активной")
    st = await statuses(session_factory, rig.acc)
    assert st[3] == "active" and st[10] == "archived" and len(st) == 10


async def test_hard_rules_screen_labels_show_current_and_next(rig: Rig) -> None:
    await active_base(rig)  # casing=lower, emoji=none
    await rig.owner.press_data("menu:style")
    await rig.owner.press("⚙️ Жёсткие правила")
    buttons = rig.owner.last.buttons
    assert "Регистр: строчные ▸ любой" in buttons
    assert "Эмодзи: нет ▸ свободно" in buttons
    assert "Ссылки: свободно ▸ нет" in buttons
    await rig.owner.press("Регистр: строчные ▸ любой")
    v2 = await rig.service.get(rig.acc, 2)
    assert v2 is not None and v2.passport.hard_rules.casing == "any"
    assert "Регистр: любой ▸ строчные" in rig.owner.last.buttons


async def test_rebuild_shows_diff_and_keeps_owner_rules(
    rig: Rig, session_factory: SF, telegram: FakeTelegram
) -> None:
    await seed_chunks(session_factory, rig.acc, LOWER_POSTS)
    await active_base(rig)
    await rig.owner.press_data("st:up")
    await send_file(rig, telegram, edited_md())
    await rig.owner.press("✅ Применить")
    rig.writer.push(model_reply(tone="новый тон"))
    await rig.owner.press_data("menu:style")
    await rig.owner.press("🔁 Пересобрать")
    shown = rig.owner.last
    assert "Тон: изменён" in shown.text
    assert {"✅ Применить", "✖️ Отменить"} <= set(shown.buttons)
    v3 = await rig.service.get(rig.acc, 3)
    assert v3 is not None and v3.status == "draft"
    assert v3.passport.owner_rules == "Пиши без клише."
    assert v3.passport.hard_rules.emoji == "rare"  # ручное из файла сохранено
    await rig.owner.press("✅ Применить")
    assert (await statuses(session_factory, rig.acc))[3] == "active"


async def test_old_callbacks_do_not_fail(rig: Rig) -> None:
    await active_base(rig)
    for data in (
        "st:rules",
        "st:hist",
        "st:ex",
        "st:keep",
        "st:pv:99",
        "st:pp:x",
        "st:upok",
        "st:nm:x",
    ):
        sent = await rig.owner.press_data(data)
        assert sent and sent[-1].text, data
