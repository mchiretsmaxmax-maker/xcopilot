"""Темы: пять тем с углом, обоснованием и форматом; дедуп за 30 дней; ручная тема."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from tests.writer_stand import OWN_POSTS, WriterStand, topics_reply


async def _old_topic(ws: WriterStand, title: str, days_ago: int) -> UUID:
    vec = ws.embedder.embed([title])[0]
    async with ws.sf() as s:
        row = await s.execute(
            text(
                "INSERT INTO topics (account_id, title, origin, dedupe_vec, created_at)"
                " VALUES (:a, :t, 'suggested', CAST(:v AS vector), :c) RETURNING id"
            ),
            {
                "a": ws.account_id,
                "t": title,
                "v": json.dumps(vec),
                "c": ws.clock.now() - timedelta(days=days_ago),
            },
        )
        await s.commit()
        return row.scalar_one()  # type: ignore[no-any-return]


async def test_suggest_topics_returns_n_with_required_fields(ws: WriterStand) -> None:
    titles = [f"тема номер {w}" for w in ("один", "два", "три", "четыре", "пять", "шесть")]
    ws.writer.push(topics_reply(*titles))

    topics = await ws.topics.suggest_topics(ws.account_id, n=5)

    assert len(topics) == 5
    for t in topics:
        assert t.title and t.angle and t.rationale
        assert t.format in ("post", "thread", "reply", "quote")
        assert t.origin == "suggested"
    assert await ws.count("topics", "origin = 'suggested' AND dedupe_vec IS NOT NULL") == 5
    assert await ws.count("events", "type = 'topic.suggested'") == 5


async def test_topics_deduped_against_last_30_days(ws: WriterStand) -> None:
    await _old_topic(ws, "блобы удешевили роллапы", days_ago=10)
    await _old_topic(ws, "стейкинг для ленивых", days_ago=40)
    ws.writer.push(
        topics_reply(
            "блобы удешевили роллапы",  # повтор темы 10-дневной давности — отброшен
            "стейкинг для ленивых",  # тема старше 30 дней — можно снова
            "мосты закрываются тихо",
            "мосты закрываются тихо",  # повтор внутри ответа — один
            "кошелёк без сид-фразы",
            "газ по выходным",
            "апгрейд и паника",
        )
    )

    topics = await ws.topics.suggest_topics(ws.account_id, n=5)

    titles = [t.title for t in topics]
    assert "блобы удешевили роллапы" not in titles
    assert "стейкинг для ленивых" in titles
    assert titles.count("мосты закрываются тихо") == 1
    assert len(titles) == 5


async def test_topics_prompt_contains_recent_own_posts(ws: WriterStand) -> None:
    ws.writer.push(topics_reply("а тема", "б тема", "в тема", "г тема", "д тема"))

    await ws.topics.suggest_topics(ws.account_id, n=5)

    prompt = ws.prompt()
    for post in OWN_POSTS:
        assert post in prompt
    assert "Крипта" in prompt  # ниша аккаунта


async def test_manual_topic_bypasses_suggestion(ws: WriterStand) -> None:
    topic = await ws.topics.add_manual_topic(ws.account_id, "мой разбор апгрейда", angle="личное")

    assert ws.writer.calls == []
    assert topic.origin == "manual"
    rows = await ws.rows(
        "SELECT title, angle, origin, status FROM topics WHERE id = :i", i=topic.id
    )
    assert tuple(rows[0]) == ("мой разбор апгрейда", "личное", "manual", "new")
