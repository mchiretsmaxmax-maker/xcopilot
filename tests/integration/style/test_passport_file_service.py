"""Паспорт как файл: загрузка, пересборка без потери ручного, названия и активация версий."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.style_stand import LOWER_POSTS, make_services, model_reply, new_account, seed_chunks
from xcopilot.db.repos import UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.parse_md import PassportParseError
from xcopilot.style.passport import HardRules, StylePassport
from xcopilot.style.render_md import export_md
from xcopilot.style.service import StyleService

SF = async_sessionmaker[AsyncSession]


async def setup(
    sf: SF, clock: FakeClock, writer: FakeLLM | None = None
) -> tuple[StyleService, UUID, UUID]:
    acc = await new_account(sf)
    async with UnitOfWork(sf) as uow:
        user, _ = await UsersRepo(uow.session).get_or_create(100, is_admin=True)
    service, _ = make_services(sf, clock, writer or FakeLLM(name="anthropic"))
    return service, acc, user.id


def base() -> StylePassport:
    return StylePassport(
        summary="база",
        tone="спокойный",
        hard_rules=HardRules(casing="lower", emoji="none"),
    )


async def test_preview_and_apply_file(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    await service.create_draft_version(acc, base())
    await service.activate(acc, 1)
    md = export_md(base()).replace("эмодзи: нет", "эмодзи: редко")
    md = md.replace("# Мои правила\n\n—", "# Мои правила\n\nПиши без клише.\nКоротко.")
    preview = await service.preview_file(acc, md)
    assert "Мои правила: +2" in preview.diff
    assert "Жёсткие: эмодзи нет → редко" in preview.diff
    assert await service.get(acc, 2) is None  # предпросмотр ничего не создаёт
    applied = await service.apply_file(acc, md, user_id=user)
    assert (applied.version, applied.status, applied.created_by) == (2, "active", "user")
    assert applied.passport.origin == "file"
    assert applied.passport.owner_rules == "Пиши без клише.\nКоротко."
    assert applied.passport.hard_rules.emoji == "rare"
    assert "emoji" in applied.passport.owner_set
    old = await service.get(acc, 1)
    assert old is not None and old.status == "archived"


async def test_bad_file_is_not_applied(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    md = export_md(base()).replace("эмодзи: нет", "эмодзи: иногда")
    with pytest.raises(PassportParseError):
        await service.preview_file(acc, md)
    with pytest.raises(PassportParseError):
        await service.apply_file(acc, md, user_id=user)
    assert await service.list_versions(acc) == []


async def test_rebuild_keeps_owner_rules_and_manual_hard_rules(
    session_factory: SF, clock: FakeClock
) -> None:
    writer = FakeLLM(name="anthropic")
    service, acc, user = await setup(session_factory, clock, writer)
    await seed_chunks(session_factory, acc, LOWER_POSTS)  # посты строчными: модель предложит lower
    md = export_md(StylePassport(tone="старый тон", owner_rules="Не про цену."))
    await service.apply_file(acc, md, user_id=user)
    await service.patch_hard_rule(acc, "casing", "any", user_id=user)  # явная правка владельца
    await service.activate(acc, 2, user_id=user)
    writer.push(model_reply(tone="новый тон"))
    rebuilt = await service.rebuild_merged(acc, user_id=user)
    p = rebuilt.version.passport
    assert rebuilt.version.status == "draft"
    assert p.tone == "новый тон"  # описательное обновилось
    assert p.owner_rules == "Не про цену."  # ручное сохранилось
    assert p.hard_rules.casing == "any"  # ручное жёсткое правило не затёрто
    assert any("регистр" in s and "строчные" in s for s in rebuilt.proposals)
    assert "Тон: изменён" in rebuilt.diff
    active = await service.get_active(acc)
    assert active is not None and active.version == 2  # пересборка ничего не активирует


async def test_rename_and_activate_any_version(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    for tone in ("один", "два", "три"):
        await service.create_draft_version(acc, StylePassport(tone=tone))
    await service.activate(acc, 3)
    named = await service.rename(acc, 1, "до чистки", user_id=user)
    assert named.passport.label == "до чистки"
    assert named.passport.tone == "один"
    back = await service.activate(acc, 1, user_id=user)  # назад
    assert back.status == "active"
    forward = await service.activate(acc, 3, user_id=user)  # и вперёд
    assert forward.status == "active"
    versions = await service.list_versions(acc)
    assert [v.version for v in versions] == [1, 2, 3]  # ничего не потеряно
    with pytest.raises(ValueError):
        await service.rename(acc, 1, "x" * 100, user_id=user)


async def test_only_owner_may_upload(session_factory: SF, clock: FakeClock) -> None:
    service, acc, user = await setup(session_factory, clock)
    assert await service.is_owner(acc, user) is False  # не участник аккаунта
