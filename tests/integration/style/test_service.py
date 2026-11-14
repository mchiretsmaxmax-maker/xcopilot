"""Версии паспорта: черновик, активация, откат, правки владельца, события (задачи 5–6)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.style_stand import make_services, new_account
from xcopilot.db.repos import UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.errors import NothingToRollback, UnknownRule
from xcopilot.style.passport import HardRules, StylePassport
from xcopilot.style.service import StyleService, describe_change, list_rules

SF = async_sessionmaker[AsyncSession]


def passport(summary: str = "голос", **rules: object) -> StylePassport:
    return StylePassport(summary=summary, hard_rules=HardRules.model_validate(rules))


async def setup(sf: SF, clock: FakeClock) -> tuple[StyleService, UUID, UUID]:
    acc = await new_account(sf)
    async with UnitOfWork(sf) as uow:
        user, _ = await UsersRepo(uow.session).get_or_create(100, is_admin=True)
    service, _ = make_services(sf, clock, FakeLLM(name="anthropic"))
    return service, acc, user.id


async def statuses(sf: SF, acc: UUID) -> dict[int, str]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT version, status FROM style_passports WHERE account_id = :a"), {"a": acc}
        )
        return {r[0]: r[1] for r in rows}


async def test_new_version_is_draft_until_activated(session_factory: SF, clock: FakeClock) -> None:
    service, acc, _ = await setup(session_factory, clock)
    v1 = await service.create_draft_version(acc, passport("первый"))
    assert (v1.version, v1.status, v1.created_by) == (1, "draft", "llm")
    assert await service.get_active(acc) is None
    v2 = await service.create_draft_version(acc, passport("второй"))
    assert v2.version == 2
    assert await statuses(session_factory, acc) == {1: "draft", 2: "draft"}
    active = await service.activate(acc, 1)
    assert active.status == "active"
    assert (await service.get_active(acc)) is not None


async def test_activate_archives_previous(session_factory: SF, clock: FakeClock) -> None:
    service, acc, _ = await setup(session_factory, clock)
    await service.create_draft_version(acc, passport("1"))
    await service.create_draft_version(acc, passport("2"))
    await service.activate(acc, 1)
    await service.activate(acc, 2)
    assert await statuses(session_factory, acc) == {1: "archived", 2: "active"}
    stored = await service.get(acc, 2)
    assert stored is not None and stored.passport.summary == "2"


async def test_rollback_restores_previous_active(session_factory: SF, clock: FakeClock) -> None:
    service, acc, _ = await setup(session_factory, clock)
    with pytest.raises(NothingToRollback):
        await service.rollback(acc)
    for n in (1, 2, 3):
        await service.create_draft_version(acc, passport(str(n)))
    await service.activate(acc, 1)
    await service.activate(acc, 2)
    await service.activate(acc, 3)
    restored = await service.rollback(acc)
    assert restored.version == 2 and restored.status == "active"
    assert await statuses(session_factory, acc) == {1: "archived", 2: "active", 3: "archived"}
    again = await service.rollback(acc)
    assert again.version == 1
    with pytest.raises(NothingToRollback):
        await service.rollback(acc)


async def test_patch_hard_rule_creates_user_version(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, passport("база", casing="lower"))
    await service.activate(acc, 1)
    patched = await service.patch_hard_rule(acc, "emoji", "none", user_id=user)
    assert (patched.version, patched.status, patched.created_by) == (2, "draft", "user")
    assert patched.passport.hard_rules.emoji == "none"
    assert patched.passport.hard_rules.casing == "lower"
    assert patched.passport.summary == "база"
    active = await service.get_active(acc)
    assert active is not None and active.version == 1  # правка не активна, пока владелец не нажмёт
    async with session_factory() as s:
        column = (
            await s.execute(
                text("SELECT hard_rules FROM style_passports WHERE account_id=:a AND version=2"),
                {"a": acc},
            )
        ).scalar_one()
    assert {"name": "emoji", "value": "none"} in column
    with pytest.raises(UnknownRule):
        await service.patch_hard_rule(acc, "nonsense", "x", user_id=user)
    with pytest.raises(ValueError):
        await service.patch_hard_rule(acc, "casing", "shout", user_id=user)


async def test_add_banned_phrase_is_case_insensitive_and_deduped(
    session_factory: SF, clock: FakeClock
) -> None:
    service, acc, user = await setup(session_factory, clock)
    first = await service.add_banned_phrase(acc, "  Delve ", user_id=user)
    assert first is not None
    assert first.passport.hard_rules.banned_phrases == ["Delve"]
    assert first.created_by == "user"
    assert await service.add_banned_phrase(acc, "DELVE", user_id=user) is None
    second = await service.add_banned_phrase(acc, "tapestry", user_id=user)
    assert second is not None
    assert second.passport.hard_rules.banned_phrases == ["Delve", "tapestry"]
    assert len(await service.list_versions(acc)) == 2


async def test_remove_rule_by_number(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    base = passport("база", casing="lower", banned_phrases=["delve", "tapestry"])
    await service.create_draft_version(acc, base)
    await service.activate(acc, 1)
    rules = list_rules(base)
    assert [name for name, _ in rules] == ["casing", "banned:delve", "banned:tapestry"]
    removed = await service.remove_rule(acc, 2, user_id=user)
    assert removed.passport.hard_rules.banned_phrases == ["tapestry"]
    reset = await service.remove_rule(acc, 1, user_id=user)  # правило-переключатель → по умолчанию
    assert reset.passport.hard_rules.casing == "any"
    with pytest.raises(UnknownRule):
        await service.remove_rule(acc, 99, user_id=user)


async def test_describe_change_counts_bans_and_rules() -> None:
    old = passport(casing="lower", emoji="none", banned_phrases=["a"])
    new = passport(casing="lower", banned_phrases=["a", "b", "c"])
    text_ = describe_change(old, new)
    assert "+2 запрета" in text_
    assert "−1 правило" in text_
    assert describe_change(None, new)
    assert describe_change(new, new) == "без изменений"


async def test_rebuild_creates_llm_draft(session_factory: SF, clock: FakeClock) -> None:
    from tests.style_stand import LOWER_POSTS, model_reply, seed_chunks

    acc = await new_account(session_factory)
    await seed_chunks(session_factory, acc, LOWER_POSTS)
    writer = FakeLLM(name="anthropic").push(model_reply())
    service, _ = make_services(session_factory, clock, writer)
    version = await service.rebuild(acc)
    assert (version.version, version.status, version.created_by) == (1, "draft", "llm")
    assert version.passport.hard_rules.casing == "lower"


async def test_passport_events_emitted(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, passport("1"))
    await service.activate(acc, 1, user_id=user)
    await service.add_banned_phrase(acc, "delve", user_id=user)
    await service.activate(acc, 2, user_id=user)
    await service.rollback(acc, user_id=user)
    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT type, props->>'version' FROM events "
                    "WHERE account_id=:a AND type LIKE 'passport.%' ORDER BY ts, id"
                ),
                {"a": acc},
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("passport.created", "1"),
        ("passport.activated", "1"),
        ("passport.created", "2"),
        ("passport.activated", "2"),
        ("passport.activated", "1"),
    ]
