"""Автопересборка паспорта (feat/clean-voice): порог новых чистых постов, частота, ручное цело.

Задача `style.auto_rebuild`: накопилось ≥ min_new новых своих постов без AI-шаблонов с прошлой
сборки (первая — ≥ min_first) и с прошлой автосборки прошло ≥ min_interval → новая версия
сразу активна, владельцу одно сообщение с кнопкой отката. Рано — задача переставляется.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.clean_voice_stand import CLEAN_POSTS, SLOP_POSTS, seed_own
from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.ingest_stand import RecordingNotifier
from tests.style_stand import make_services, model_reply, new_account
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.auto import (
    AUTO_REBUILD_KIND,
    AutoConfig,
    load_auto_config,
    request_auto_rebuild,
    run_auto_rebuild,
)
from xcopilot.style.errors import NoOwnPosts
from xcopilot.style.parse_md import OWNER_CHUNK
from xcopilot.style.passport import Example, HardRules, StylePassport
from xcopilot.style.service import StyleService

SF = async_sessionmaker[AsyncSession]
T0 = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
CFG = AutoConfig(min_new=5, min_first=5, min_interval=timedelta(hours=12))


def posts(tag: str, n: int) -> list[str]:
    return [f"{p} ({tag}{i})" for i, p in zip(range(n), CLEAN_POSTS * 3, strict=False)]


def writer(n: int = 3) -> FakeLLM:
    return FakeLLM(name="codex_cli").push(*[model_reply() for _ in range(n)])


async def run(
    sf: SF, service: StyleService, clock: FakeClock, notifier: RecordingNotifier, acc: UUID
) -> object:
    return await run_auto_rebuild(
        account_id=acc,
        service=service,
        uow_factory=lambda: UnitOfWork(sf),
        clock=clock,
        notifier=notifier,
        config=CFG,
    )


async def queued(sf: SF, acc: UUID) -> list[datetime]:
    async with sf() as s:
        rows = await s.execute(
            text(
                "SELECT run_at FROM jobs WHERE account_id=:a AND kind=:k AND status='queued'"
                " ORDER BY run_at"
            ),
            {"a": acc, "k": AUTO_REBUILD_KIND},
        )
        return [r[0] for r in rows]


async def owner_passport(service: StyleService, acc: UUID) -> int:
    """Активная версия владельца с ручным: «Мои правила», casing в owner_set, свой пример."""
    base = StylePassport(
        summary="старый русский паспорт",
        hard_rules=HardRules(casing="lower", banned_phrases=["game changer"]),
        owner_rules="пиши коротко",
        format_rules={"reply": "без вопросов в конце"},
        owner_set=["casing", "banned_phrases"],
        examples=[Example(chunk_id=OWNER_CHUNK, text="мой пример")],
    )
    created = await service.create_draft_version(acc, base, created_by="user")
    await service.activate(acc, created.version)
    return created.version


def test_config_file_has_thresholds() -> None:
    cfg = load_auto_config()
    assert cfg.min_new >= 1 and cfg.min_first >= cfg.min_new
    assert cfg.min_interval >= timedelta(hours=1)


async def test_below_threshold_does_nothing(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    service, _ = make_services(session_factory, clock, writer())
    v = await owner_passport(service, acc)
    await seed_own(session_factory, acc, posts("a", 4) + SLOP_POSTS)  # грязные не считаются
    notifier = RecordingNotifier()
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "below_threshold"  # type: ignore[attr-defined]
    assert result.new == 4  # type: ignore[attr-defined]
    assert (await service.get_active(acc)).version == v  # type: ignore[union-attr]
    assert notifier.sent == []


async def test_threshold_rebuilds_activates_and_keeps_owner_rules(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    llm = writer()
    service, _ = make_services(session_factory, clock, llm)
    old = await owner_passport(service, acc)
    clean = await seed_own(session_factory, acc, posts("b", 5))
    await seed_own(session_factory, acc, SLOP_POSTS)
    notifier = RecordingNotifier()
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "built"  # type: ignore[attr-defined]
    active = await service.get_active(acc)
    assert active is not None and active.version == old + 1
    p = active.passport
    assert p.owner_rules == "пиши коротко"
    assert p.format_rules == {"reply": "без вопросов в конце"}
    assert p.hard_rules.casing == "lower" and "game changer" in p.hard_rules.banned_phrases
    assert set(p.owner_set) >= {"casing", "banned_phrases"}
    assert any(e.chunk_id == OWNER_CHUNK for e in p.examples)
    assert set(p.built_from) == {str(s) for s in clean}
    assert p.built_at == T0
    versions = {v.version: v.status for v in await service.list_versions(acc)}
    assert versions[old] == "archived"  # старая — в истории, откат кнопкой
    sent = " ".join(m.content for m in llm.calls[0].request.messages)
    assert all(s not in sent for s in SLOP_POSTS)
    assert len(notifier.sent) == 1
    _, msg, keyboard = notifier.sent[0]
    assert msg.startswith("🪪 Паспорт обновлён из 5 новых постов")
    buttons = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "st:rb" in buttons


async def test_only_posts_new_since_last_build_count(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    service, _ = make_services(session_factory, clock, writer())
    await owner_passport(service, acc)
    await seed_own(session_factory, acc, posts("c", 5))
    notifier = RecordingNotifier()
    await run(session_factory, service, clock, notifier, acc)
    clock.advance(timedelta(hours=13).total_seconds())
    await seed_own(session_factory, acc, posts("d", 2))
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "below_threshold"  # type: ignore[attr-defined]
    assert result.new == 2  # type: ignore[attr-defined]


async def test_rollback_is_respected_until_new_posts(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    service, _ = make_services(session_factory, clock, writer())
    old = await owner_passport(service, acc)
    await seed_own(session_factory, acc, posts("e", 5))
    notifier = RecordingNotifier()
    await run(session_factory, service, clock, notifier, acc)
    await service.rollback(acc)
    clock.advance(timedelta(hours=13).total_seconds())
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "below_threshold"  # type: ignore[attr-defined]
    assert (await service.get_active(acc)).version == old  # type: ignore[union-attr]


async def test_too_soon_reschedules(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    service, _ = make_services(session_factory, clock, writer())
    await owner_passport(service, acc)
    await seed_own(session_factory, acc, posts("f", 5))
    notifier = RecordingNotifier()
    first = await run(session_factory, service, clock, notifier, acc)
    assert first.reason == "built"  # type: ignore[attr-defined]
    clock.advance(timedelta(hours=1).total_seconds())
    await seed_own(session_factory, acc, posts("g", 5))
    second = await run(session_factory, service, clock, notifier, acc)
    assert second.reason == "too_soon"  # type: ignore[attr-defined]
    assert await queued(session_factory, acc) == [T0 + timedelta(hours=12)]
    assert len(notifier.sent) == 1
    clock.advance(timedelta(hours=11).total_seconds())
    third = await run(session_factory, service, clock, notifier, acc)
    assert third.reason == "built"  # type: ignore[attr-defined]
    assert third.new == 5  # type: ignore[attr-defined]


async def test_first_build_needs_min_first(session_factory: SF) -> None:
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    service, _ = make_services(session_factory, clock, writer())
    await seed_own(session_factory, acc, posts("h", 4))
    notifier = RecordingNotifier()
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "below_threshold"  # type: ignore[attr-defined]
    assert await service.list_versions(acc) == []
    await seed_own(session_factory, acc, posts("i", 1))
    result = await run(session_factory, service, clock, notifier, acc)
    assert result.reason == "built"  # type: ignore[attr-defined]
    active = await service.get_active(acc)
    assert active is not None and active.version == 1


async def test_request_keeps_one_queued_job(session_factory: SF) -> None:
    acc = await new_account(session_factory)
    async with UnitOfWork(session_factory) as uow:
        first = await request_auto_rebuild(uow.session, acc, now=T0, config=CFG)
        again = await request_auto_rebuild(uow.session, acc, now=T0, config=CFG)
    assert first is not None and again is None
    assert await queued(session_factory, acc) == [T0 + CFG.delay]


async def test_extract_with_only_slop_posts_fails(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    await seed_own(session_factory, acc, SLOP_POSTS)
    _, extractor = make_services(session_factory, clock, writer())
    with pytest.raises(NoOwnPosts):
        await extractor.extract(acc)


async def test_too_few_clean_posts_keeps_passport(session_factory: SF) -> None:
    """Решение главного чата 19.11: чистых меньше min_first — паспорт не трогаем, сообщаем."""
    clock = FakeClock(T0)
    acc = await new_account(session_factory)
    llm = writer()
    service, _ = make_services(session_factory, clock, llm)
    old = await owner_passport(service, acc)
    await seed_own(session_factory, acc, posts("j", 5) + SLOP_POSTS * 2)
    notifier = RecordingNotifier()
    cfg = AutoConfig(min_new=5, min_first=10, min_interval=timedelta(hours=12))
    result = await run_auto_rebuild(
        account_id=acc,
        service=service,
        uow_factory=lambda: UnitOfWork(session_factory),
        clock=clock,
        notifier=notifier,
        config=cfg,
    )
    assert result.reason == "too_few_clean"
    assert (await service.get_active(acc)).version == old  # type: ignore[union-attr]
    assert len(await service.list_versions(acc)) == 1
    assert llm.calls == []
    assert [m for _, m, _ in notifier.sent] == [
        "🪪 Мало чистых постов: 5. Паспорт не пересобираю, текущий остаётся."
    ]
