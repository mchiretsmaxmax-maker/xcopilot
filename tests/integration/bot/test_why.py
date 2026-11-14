"""/why: что увидел писатель в последнем запуске — примеры, уроки, факты, версии."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.bot.conftest import OWNER, Client, count, make_onboarded
from tests.retrieval_stand import FakeKnowledge, Kb, kitem
from xcopilot.bot.deps import BotDeps
from xcopilot.retrieval.context_pack import TaskSpec, build_pack

SF = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 14, tzinfo=UTC)
KNOWLEDGE = FakeKnowledge([kitem(1, slot="hook")])


async def seed_run(sf: SF, kb: Kb, acc: UUID, *, long_text: bool = False) -> None:
    await kb.passport(acc)
    body = "L2 мой пост про блобы" + (" длинный" * 200 if long_text else "")
    await kb.chunk(acc, body, kind="own_post")
    await kb.draft(acc, "Отклонённый черновик про L2")
    await kb.fact(acc, "Комиссии L2 упали на 90%", expires_at=NOW + timedelta(days=9))
    async with sf() as s:
        pack = await build_pack(
            s,
            acc,
            TaskSpec(format="post", lang="ru", topic="L2 блобы"),
            embedder=kb.embedder,
            now=NOW,
            knowledge=KNOWLEDGE,
        )
        prompt_id = (
            await s.execute(
                text(
                    "INSERT INTO prompts (key, version, role, template, content_hash) "
                    "VALUES ('writer.post', 2, 'writer', 't', 'h') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO generation_runs (account_id, kind, context_pack, passport_version, "
                "prompt_id, status) VALUES (:a, 'post', CAST(:c AS jsonb), :v, :p, 'done')"
            ),
            {
                "a": acc,
                "c": json.dumps(pack.to_ids().model_dump(mode="json")),
                "v": pack.passport_version,
                "p": prompt_id,
            },
        )
        await s.commit()


async def test_why_command_lists_pack_contents(
    owner: Client, session_factory: SF, deps: BotDeps
) -> None:
    deps.knowledge = KNOWLEDGE
    acc = await make_onboarded(session_factory, OWNER, "acc")
    kb = Kb(session_factory)
    await seed_run(session_factory, kb, acc)
    await owner.say("/why")
    reply = owner.last.text
    assert "L2 мой пост про блобы" in reply
    assert "Приём 1" in reply  # раздел знаний
    assert "Отклонённый черновик про L2" in reply  # антипример
    assert "Комиссии L2 упали на 90%" in reply
    assert "Паспорт: v1" in reply
    assert "Промпт: writer.post v2" in reply
    assert await count(session_factory, "events", "props->>'action' = 'why'") == 1


async def test_why_fits_telegram_limit(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory, OWNER, "acc")
    await seed_run(session_factory, Kb(session_factory), acc, long_text=True)
    await owner.say("/why")
    assert len(owner.last.text) <= 4096
    assert "Паспорт: v1" in owner.last.text


async def test_why_without_runs_says_so(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory, OWNER, "acc")
    await owner.say("/why")
    assert "нет ни одного запуска" in owner.last.text
