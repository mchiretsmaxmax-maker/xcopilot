"""Разовый скрипт scripts/oneoff/clean_voice.py (feat/clean-voice) на тестовой БД.

Чужие посты, лежащие как свои, → inspiration (доказательства пересматриваются задачей
brain.detect с revise); отчёт фильтра AI-шаблонов; пересборка паспорта (ручное цело).
`--dry-run` ничего не меняет и модель не зовёт. Повторный запуск ничего не меняет.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.clean_voice_stand import CLEAN_POSTS, SLOP_POSTS, seed_own
from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.style_stand import make_services, model_reply
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.passport import HardRules, StylePassport

SF = async_sessionmaker[AsyncSession]
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "oneoff" / "clean_voice.py"
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clean_voice_oneoff", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass ищет модуль класса в sys.modules
    spec.loader.exec_module(mod)
    return mod


async def setup(sf: SF) -> tuple[UUID, list[UUID], list[UUID]]:
    """Аккаунт prima_t: 7 чистых своих, 3 грязных своих, 2 чужих как own (один разобран)."""
    async with UnitOfWork(sf) as uow:
        acc = (await AccountsRepo(uow.session).create(handle="prima_t", tz="UTC")).id
    mine = await seed_own(sf, acc, CLEAN_POSTS) + await seed_own(sf, acc, SLOP_POSTS)
    foreign = await seed_own(sf, acc, ["their great post one", "their great post two"])
    async with sf() as s:
        await s.execute(
            text("UPDATE sources SET author_handle = 'prima_t' WHERE id = ANY(:i)"), {"i": mine}
        )
        await s.execute(
            text("UPDATE sources SET author_handle = 'Viktoroddy' WHERE id = :i"),
            {"i": foreign[0]},
        )
        await s.execute(
            text("UPDATE sources SET author_handle = 'poteto' WHERE id = :i"), {"i": foreign[1]}
        )
        await s.execute(
            text("UPDATE sources SET analysis_status = 'analyzed' WHERE id = :i"),
            {"i": foreign[0]},
        )
        await s.commit()
    return acc, mine, foreign


async def snapshot(sf: SF, acc: UUID) -> tuple[list[tuple[str, str]], int, int]:
    async with sf() as s:
        stances = (
            await s.execute(
                text("SELECT id::text, stance FROM sources WHERE account_id=:a ORDER BY id"),
                {"a": acc},
            )
        ).all()
        jobs = (
            await s.execute(text("SELECT count(*) FROM jobs WHERE account_id=:a"), {"a": acc})
        ).scalar_one()
        passports = (
            await s.execute(
                text("SELECT count(*) FROM style_passports WHERE account_id=:a"), {"a": acc}
            )
        ).scalar_one()
    return [(r[0], r[1]) for r in stances], int(jobs), int(passports)


async def owner_v1(sf: SF, clock: FakeClock, llm: FakeLLM, acc: UUID) -> None:
    service, _ = make_services(sf, clock, llm)
    base = StylePassport(owner_rules="мои правила", hard_rules=HardRules(casing="lower"))
    created = await service.create_draft_version(
        acc, base.model_copy(update={"owner_set": ["casing"]}), created_by="user"
    )
    await service.activate(acc, created.version)


async def test_dry_run_changes_nothing_and_reports(session_factory: SF) -> None:
    mod = load()
    clock = FakeClock(T0)
    acc, _, _ = await setup(session_factory)
    llm = FakeLLM(name="anthropic")
    await owner_v1(session_factory, clock, llm, acc)
    service, _ = make_services(session_factory, clock, llm)
    before = await snapshot(session_factory, acc)
    lines: list[str] = []
    report = await mod.run(
        lambda: UnitOfWork(session_factory),
        account="prima_t",
        handles=mod.FOREIGN_HANDLES,
        dry_run=True,
        style=service,
        clock=clock,
        out=lines.append,
    )
    assert await snapshot(session_factory, acc) == before
    assert llm.calls == []
    assert len(report.restanced) == 2
    assert report.clean == 7 and len(report.dirty) == 3
    assert report.built is None
    output = "\n".join(lines)
    assert "DRY-RUN" in output
    assert "viktoroddy" in output.lower() and "poteto" in output
    assert "em_dash" in output and "binary_contrast" in output


async def test_run_restances_rebuilds_and_is_idempotent(session_factory: SF) -> None:
    mod = load()
    clock = FakeClock(T0)
    acc, mine, foreign = await setup(session_factory)
    llm = FakeLLM(name="anthropic").push(model_reply(), model_reply())
    await owner_v1(session_factory, clock, llm, acc)
    service, _ = make_services(session_factory, clock, llm)

    async def go() -> object:
        return await mod.run(
            lambda: UnitOfWork(session_factory),
            account="prima_t",
            handles=mod.FOREIGN_HANDLES,
            dry_run=False,
            style=service,
            clock=clock,
            out=lambda _: None,
            min_first=5,
        )

    first = await go()
    stances = dict((await snapshot(session_factory, acc))[0])
    assert {stances[str(f)] for f in foreign} == {"inspiration"}
    assert {stances[str(m)] for m in mine} == {"own"}  # грязные свои остаются own
    async with session_factory() as s:
        revise = (
            (
                await s.execute(
                    text("SELECT payload FROM jobs WHERE account_id=:a AND kind='brain.detect'"),
                    {"a": acc},
                )
            )
            .scalars()
            .all()
        )
    assert [(p["source_id"], p["revise"]) for p in revise] == [(str(foreign[0]), True)]
    assert first.built == 2  # type: ignore[attr-defined]
    active = await service.get_active(acc)
    assert active is not None and active.version == 2
    assert active.passport.owner_rules == "мои правила"
    assert active.passport.hard_rules.casing == "lower"
    assert set(active.passport.built_from) == {str(m) for m in mine[:7]}
    assert len(llm.calls) == 1

    second = await go()
    assert second.restanced == []  # type: ignore[attr-defined]
    assert second.built is None  # type: ignore[attr-defined]
    assert len(llm.calls) == 1
    assert (await snapshot(session_factory, acc))[2] == 2


async def test_too_few_clean_posts_keeps_passport(session_factory: SF) -> None:
    """Чистых меньше min_first (по умолчанию 10, здесь 7) — пересборки нет, паспорт цел."""
    mod = load()
    clock = FakeClock(T0)
    acc, _, _ = await setup(session_factory)
    llm = FakeLLM(name="anthropic").push(model_reply())
    await owner_v1(session_factory, clock, llm, acc)
    service, _ = make_services(session_factory, clock, llm)
    lines: list[str] = []
    report = await mod.run(
        lambda: UnitOfWork(session_factory),
        account="prima_t",
        handles=mod.FOREIGN_HANDLES,
        dry_run=False,
        style=service,
        clock=clock,
        out=lines.append,
    )
    assert report.built is None
    assert llm.calls == []
    assert (await snapshot(session_factory, acc))[2] == 1
    assert any("мало чистых постов: 7" in line for line in lines)
    assert any("правило ai_slop/em_dash" in line for line in lines)


async def test_foreign_handle_with_owner_url_stays_own(session_factory: SF) -> None:
    """Цитата владельца: автор @claudeai, но url — пост владельца → не переносится (19.11)."""
    mod = load()
    clock = FakeClock(T0)
    acc, _, foreign = await setup(session_factory)
    quote = (await seed_own(session_factory, acc, ["my quote of the claude launch"]))[0]
    async with session_factory() as s:
        await s.execute(
            text(
                "UPDATE sources SET author_handle = 'claudeai',"
                " url = 'https://x.com/Prima_T/status/123' WHERE id = :i"
            ),
            {"i": quote},
        )
        await s.commit()
    lines: list[str] = []
    report = await mod.run(
        lambda: UnitOfWork(session_factory),
        account="prima_t",
        handles=mod.FOREIGN_HANDLES,
        dry_run=False,
        style=None,
        clock=clock,
        out=lines.append,
    )
    assert set(report.restanced) == set(foreign)
    stances = dict((await snapshot(session_factory, acc))[0])
    assert stances[str(quote)] == "own"
    assert any("x.com/Prima_T/status/123" in line and "владельца" in line for line in lines)


async def test_force_rebuild_rebuilds_from_same_posts(session_factory: SF) -> None:
    """--force-rebuild: пересборка, даже если паспорт уже собран из тех же постов (чинит v7)."""
    mod = load()
    clock = FakeClock(T0)
    acc, _, _ = await setup(session_factory)
    llm = FakeLLM(name="anthropic").push(model_reply(), model_reply())
    await owner_v1(session_factory, clock, llm, acc)
    service, _ = make_services(session_factory, clock, llm)

    async def go(force: bool) -> object:
        return await mod.run(
            lambda: UnitOfWork(session_factory),
            account="prima_t",
            handles=mod.FOREIGN_HANDLES,
            dry_run=False,
            style=service,
            clock=clock,
            out=lambda _: None,
            min_first=5,
            force_rebuild=force,
        )

    assert (await go(False)).built == 2  # type: ignore[attr-defined]
    assert (await go(False)).built is None  # type: ignore[attr-defined]
    assert (await go(True)).built == 3  # type: ignore[attr-defined]
    assert len(llm.calls) == 2
