"""«🎨 Стиль»: переключатели сразу активны, «📝 Правила по форматам» (feat/style-formats)."""

from __future__ import annotations

from tests.integration.bot.conftest import Client
from tests.integration.bot.test_style import Rig, base, rig, statuses  # noqa: F401


async def _active(rig: Rig) -> None:  # noqa: F811
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)


async def test_toggle_applies_at_once(rig: Rig, session_factory: object) -> None:  # noqa: F811
    await _active(rig)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("⚙️ Жёсткие правила")
    await rig.owner.press_data("st:tg:emoji")  # нет → свободно
    assert "Применено: эмодзи → свободно (v2)" in rig.owner.last.text
    assert "✅ Сделать активной" not in " ".join(rig.owner.last.buttons)
    await rig.owner.press_data("st:tg:links")
    assert "(v3)" in rig.owner.last.text
    active = await rig.service.get_active(rig.acc)
    assert active is not None and active.version == 3
    assert active.passport.hard_rules.emoji == "free"
    assert active.passport.hard_rules.links == "none"


async def test_format_rules_edit_flow(rig: Rig) -> None:  # noqa: F811
    await _active(rig)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("📝 Правила по форматам")
    await rig.owner.press_data("st:fmt:reply")
    assert "ответ" in rig.owner.last.text.lower()
    assert "✏️ Изменить" in rig.owner.last.buttons
    await rig.owner.press("✏️ Изменить")
    await rig.owner.say("Не спорь.\nОтвечай по делу.")
    active = await rig.service.get_active(rig.acc)
    assert active is not None and active.version == 2
    assert active.passport.format_rules == {"reply": "Не спорь.\nОтвечай по делу."}


async def test_format_rules_edit_cancel(rig: Rig) -> None:  # noqa: F811
    await _active(rig)
    await rig.owner.press_data("menu:style")
    await rig.owner.press("📝 Правила по форматам")
    await rig.owner.press_data("st:fmt:post")
    await rig.owner.press("✏️ Изменить")
    await rig.owner.press("✖️ Отменить")
    await rig.owner.say("просто текст")
    active = await rig.service.get_active(rig.acc)
    assert active is not None and active.version == 1
    assert active.passport.format_rules == {}
