"""Ветки «🎨 Стиль» вне основного сценария: пустые состояния, ошибки, переключатели."""

from __future__ import annotations

from tests.integration.bot.conftest import Client
from tests.integration.bot.test_style import Rig, base, rig, statuses  # noqa: F401
from xcopilot.style.passport import StylePassport


async def test_empty_states_without_passport(rig: Rig) -> None:  # noqa: F811
    owner = rig.owner
    await owner.press_data("menu:style")
    for data, needle in (
        ("st:rules", "паспорта пока нет"),
        ("st:ex", "примеров пока нет"),
        ("st:hist", "паспорта пока нет"),
        ("st:md", "паспорта пока нет"),
        ("st:tg:emoji", "паспорта пока нет"),
        ("st:act:7", "паспорта пока нет"),
        ("st:act:xx", "паспорта пока нет"),
        ("st:rb", "некуда"),
    ):
        await owner.press_data(data)
        assert needle in owner.last.text.lower(), data


async def test_draft_only_home_and_toggles(rig: Rig) -> None:  # noqa: F811
    await rig.service.create_draft_version(rig.acc, StylePassport())
    await rig.owner.press_data("menu:style")
    assert "черновик v1" in rig.owner.last.text
    await rig.owner.press_data("st:tg:casing")
    stored = await rig.service.get(rig.acc, 2)
    assert stored is not None and stored.passport.hard_rules.casing == "lower"
    await rig.owner.press_data("st:tg:emoji")
    stored = await rig.service.get(rig.acc, 3)
    assert stored is not None and stored.passport.hard_rules.emoji == "none"
    assert stored.passport.hard_rules.casing == "lower"  # правки копятся
    await rig.owner.press_data("st:tg:emoji")
    stored = await rig.service.get(rig.acc, 4)
    assert stored is not None and stored.passport.hard_rules.emoji == "free"
    await rig.owner.press_data("st:tg:bogus")
    assert (await rig.service.get(rig.acc, 5)) is None


async def test_keep_activate_and_history_branches(rig: Rig, session_factory: object) -> None:  # noqa: F811
    await rig.service.create_draft_version(rig.acc, base())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("menu:style")
    assert "Откатить" not in " ".join((await rig.owner.press_data("st:hist"))[-1].buttons)
    await rig.owner.press_data("st:keep")
    assert "v1" in rig.owner.last.text
    await rig.owner.press_data("st:act:1")
    assert "активен" in rig.owner.last.text


async def test_rebuild_unavailable_and_rules_view(rig: Rig) -> None:  # noqa: F811
    await rig.service.create_draft_version(rig.acc, StylePassport())
    await rig.service.activate(rig.acc, 1)
    await rig.owner.press_data("st:rules")
    assert "—" in rig.owner.last.text
    await rig.owner.say("убери правило 1")
    assert "нет правила" in rig.owner.last.text.lower()


async def test_no_account_gets_hint(session_factory: object, dp: object, telegram: object) -> None:
    from tests.integration.bot.conftest import OWNER

    client = Client(dp, telegram, OWNER)  # type: ignore[arg-type]
    await client.press_data("menu:style")
    assert client.last.text
    await client.say("добавь запрет: x")
    await client.say("убери правило 1")
    await client.press_data("st:rebuild")
    await client.press_data("st:rules")
    assert client.last.text
