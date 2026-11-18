"""Переключатели действуют сразу; format_rules переживают пересборку (feat/style-formats)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.integration.style.test_passport_file_service import base, setup
from tests.style_stand import LOWER_POSTS, model_reply, seed_chunks
from xcopilot.style.passport import StylePassport

SF = async_sessionmaker[AsyncSession]


async def test_toggle_is_active_immediately(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, base())
    await service.activate(acc, 1)
    created = await service.patch_hard_rule(acc, "emoji", "free", user_id=user)
    assert (created.version, created.status, created.created_by) == (2, "active", "user")
    assert created.passport.hard_rules.emoji == "free"
    assert "emoji" in created.passport.owner_set
    old = await service.get(acc, 1)
    assert old is not None and old.status == "archived"
    active = await service.get_active(acc)
    assert active is not None and active.version == 2


async def test_each_toggle_is_own_version_and_no_empty_versions(
    session_factory: SF, clock: FakeClock
) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, base())
    await service.activate(acc, 1)
    await service.patch_hard_rule(acc, "emoji", "free", user_id=user)
    await service.patch_hard_rule(acc, "links", "none", user_id=user)
    same = await service.patch_hard_rule(acc, "links", "none", user_id=user)  # ничего не меняет
    assert same.version == 3
    assert [v.version for v in await service.list_versions(acc)] == [1, 2, 3]


async def test_set_format_rules_creates_active_version(
    session_factory: SF, clock: FakeClock
) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, base())
    await service.activate(acc, 1)
    v = await service.set_format_rules(acc, "reply", "Коротко.", user_id=user)
    assert (v.version, v.status, v.created_by) == (2, "active", "user")
    assert v.passport.format_rules == {"reply": "Коротко."}
    same = await service.set_format_rules(acc, "reply", "Коротко.", user_id=user)
    assert same.version == 2  # без изменений — без новой версии
    cleared = await service.set_format_rules(acc, "reply", "", user_id=user)
    assert cleared.passport.format_rules == {}


async def test_rebuild_keeps_format_rules(session_factory: SF, clock: FakeClock) -> None:
    writer = FakeLLM(name="anthropic")
    service, acc, user = await setup(session_factory, clock, writer)
    await seed_chunks(session_factory, acc, LOWER_POSTS)
    await service.create_draft_version(acc, StylePassport(tone="старый"))
    await service.activate(acc, 1)
    await service.set_format_rules(acc, "thread", "Не больше пяти частей.", user_id=user)
    writer.push(model_reply(tone="новый тон"))
    rebuilt = await service.rebuild_merged(acc, user_id=user)
    assert rebuilt.version.passport.tone == "новый тон"
    assert rebuilt.version.passport.format_rules == {"thread": "Не больше пяти частей."}


async def test_file_diff_shows_format_changes(session_factory: SF, clock: FakeClock) -> None:
    from xcopilot.style.render_md import export_md

    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, base())
    await service.activate(acc, 1)
    md = export_md(base()).replace(
        "# Правила для ответов\n\n", "# Правила для ответов\n\nНе спорь.\n\n"
    )
    preview = await service.preview_file(acc, md)
    assert "Правила для ответов: изменены" in preview.diff
    applied = await service.apply_file(acc, md, user_id=user)
    assert applied.passport.format_rules == {"reply": "Не спорь."}
    _: UUID = acc
