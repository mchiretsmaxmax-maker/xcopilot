"""Импорт своей истории (этап 7A, задача 7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

from tests.brain_stand import BrainStand
from tests.fakes import FakeX
from xcopilot.errors import BudgetExceeded
from xcopilot.x.models import XMetrics, XPost


def _own(brain: BrainStand, n: int, *, start: int = 1) -> list[XPost]:
    posts = []
    for i in range(start, start + n):
        views = 1000 + 37 * i
        p = XPost(
            id=str(10_000 + i),
            text=(
                f"{i} lessons from shipping\nshort line\nshort line"
                if i % 3 == 0
                else f"Long introduction about markets and life number {i} that keeps going on"
            ),
            author_handle="me",
            created_at=brain.clock.now() - timedelta(days=2 + i % 80),
            source="api",
            metrics=XMetrics(
                likes=views // 40,
                reposts=views // 300,
                replies=views // 600,
                quotes=1,
                bookmarks=views // 500,
                impressions=views,
            ),
        )
        brain.x.add_post(p, own=True)
        posts.append(p)
    return posts


async def test_history_import_creates_own_sources_with_signals(brain: BrainStand) -> None:
    _own(brain, 30)
    result = await brain.importer.import_own_history(brain.account_id, limit=500)
    assert (result.imported, result.skipped, result.stopped_by_budget) == (30, 0, False)
    assert result.text.startswith("Загрузил 30 твоих постов.")
    assert "/brain" in result.text

    rows = await brain.rows(
        "SELECT s.stance, s.origin, s.kind, s.analysis_status, s.external_id, s.posted_at,"
        " (SELECT count(*) FROM documents d WHERE d.source_id = s.id) AS docs"
        " FROM sources s WHERE s.account_id = :a",
        a=brain.account_id,
    )
    assert len(rows) == 30
    assert {(r.stance, r.origin, r.kind, r.analysis_status) for r in rows} == {
        ("own", "history_backfill", "post", "pending")
    }
    assert all(r.docs == 1 and r.posted_at is not None for r in rows)

    sig = await brain.rows(
        "SELECT m.origin, m.views, m.likes, m.bookmarks, m.post_age_hours"
        " FROM material_signals m JOIN sources s ON s.id = m.source_id"
        " WHERE s.account_id = :a AND s.external_id = '10001'",
        a=brain.account_id,
    )
    assert len(sig) == 1
    assert (sig[0].origin, sig[0].views, sig[0].likes) == ("own_sync", 1037, 1037 // 40)
    assert float(sig[0].post_age_hours) >= 24

    # исходы посчитаны (30 своих постов ≥ 20 → перцентиль), разбор поставлен в очередь
    assert (
        await brain.count(
            "material_outcomes",
            "account_id = :a AND is_current AND basis = 'own_baseline'",
            a=brain.account_id,
        )
        == 30
    )
    assert (
        await brain.count("jobs", "account_id = :a AND kind = 'brain.analyze'", a=brain.account_id)
        == 30
    )
    events = await brain.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'history.imported'",
        a=brain.account_id,
    )
    assert events[0].props["imported"] == 30


async def test_history_import_idempotent(brain: BrainStand) -> None:
    _own(brain, 12)
    first = await brain.importer.import_own_history(brain.account_id)
    _own(brain, 3, start=13)
    second = await brain.importer.import_own_history(brain.account_id)
    assert (first.imported, second.imported, second.skipped) == (12, 3, 12)
    assert await brain.count("sources", "account_id = :a", a=brain.account_id) == 15
    assert (
        await brain.count(
            "material_signals", "account_id = :a AND origin = 'own_sync'", a=brain.account_id
        )
        == 15
    )
    assert (
        await brain.count("jobs", "account_id = :a AND kind = 'brain.analyze'", a=brain.account_id)
        == 15
    )


@dataclass
class _MeteredX(FakeX):
    """Как XService: бюджет кончается посреди чтения истории."""

    budget_posts: int = 0

    async def iter_own_history(self, limit: int = 500) -> AsyncIterator[XPost]:
        n = 0
        async for p in super().iter_own_history(limit):
            if n >= self.budget_posts:
                raise BudgetExceeded("x_api: лимит исчерпан")
            n += 1
            yield p


async def test_history_import_respects_budget(brain: BrainStand) -> None:
    # бюджета нет совсем — X не вызывается
    _own(brain, 10)
    brain.budget.scope_limits["x_api"] = 0
    empty = await brain.importer.import_own_history(brain.account_id, limit=500)
    assert (empty.imported, empty.stopped_by_budget) == (0, True)
    assert not any(c.startswith("iter_own_history") for c in brain.x.calls)
    assert brain.budget.checks and brain.budget.checks[0][1] == "x_api"
    assert "бюджет" in empty.text.lower()

    # бюджет кончился посреди чтения — сохраняем прочитанное
    metered = _MeteredX(budget_posts=4)
    for p in brain.x.own[::-1]:
        metered.add_post(p, own=True)
    brain.budget.scope_limits.clear()

    async def x_for(_: object) -> FakeX:
        return metered

    brain.importer._x_for = x_for
    partial = await brain.importer.import_own_history(brain.account_id, limit=500)
    assert (partial.imported, partial.stopped_by_budget) == (4, True)
    assert await brain.count("sources", "account_id = :a", a=brain.account_id) == 4
