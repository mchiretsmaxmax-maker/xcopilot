"""Этап 14, задачи 1–4: синк своей ленты, снимки, чекпоинты, сверка (ТЗ «Тесты (до кода)»)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import respx

from tests.analytics_stand import AStand, OwnerNotifier
from tests.fakes import FakeBudgetGuard, FakeSleep
from tests.integration.x.conftest import OWN_USER_ID
from xcopilot.analytics.sync import OwnSync
from xcopilot.x.api_v2 import API_BASE, EP_USER_POSTS, XApiV2
from xcopilot.x.client import XService
from xcopilot.x.errors import XAuthError
from xcopilot.x.models import XPost
from xcopilot.x.oembed import OEmbedReader


async def test_sync_inserts_new_posts_and_snapshots(st: AStand) -> None:
    st.own_post("Первый пост про рынок", age=timedelta(hours=2), post_id="101")
    st.own_post("Второй пост про газ", age=timedelta(hours=30), post_id="102")
    res = await st.sync.sync_own_posts(st.account_id)
    assert (res.new_posts, res.skipped) == (2, None)
    src = await st.rows(
        "SELECT external_id, stance, origin, kind, status FROM sources WHERE account_id = :a",
        a=st.account_id,
    )
    assert {tuple(r) for r in src} == {
        ("101", "own", "x_api", "post", "parsed"),
        ("102", "own", "x_api", "post", "parsed"),
    }
    assert await st.count("documents", "account_id = :a", a=st.account_id) == 2
    snaps = await st.rows(
        "SELECT x_post_id, age_minutes, impressions FROM post_metric_snapshots"
        " WHERE account_id = :a ORDER BY x_post_id",
        a=st.account_id,
    )
    assert [tuple(r) for r in snaps] == [("101", 120, 1000), ("102", 1800, 1000)]
    assert await st.count("material_signals", "origin = 'own_sync'") == 2
    assert len(await st.jobs("ingest.process")) == 2  # куски, теги, векторы
    assert len(await st.jobs("brain.analyze")) == 2


async def test_sync_uses_since_id(st: AStand) -> None:
    st.own_post("Пост раз", post_id="101")
    st.own_post("Пост два", post_id="102")
    await st.sync.sync_own_posts(st.account_id)
    st.own_post("Пост три", post_id="103")
    res = await st.sync.sync_own_posts(st.account_id)
    assert res.new_posts == 1
    assert st.x.calls == ["get_own_posts:None:100", "get_own_posts:102:100"]


def _tweet(id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "text": f"Свой пост {id_}",
        "author_id": OWN_USER_ID,
        "created_at": "2026-11-14T10:00:00.000Z",
        "public_metrics": {
            "retweet_count": 1,
            "reply_count": 0,
            "like_count": 5,
            "quote_count": 0,
            "bookmark_count": 0,
            "impression_count": 400,
        },
    }


async def test_sync_cost_recorded_as_owned(st: AStand) -> None:
    async def token() -> str:
        return "t"

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API_BASE}/2/users/{OWN_USER_ID}/tweets").mock(
            return_value=httpx.Response(
                200, json={"data": [_tweet("202"), _tweet("201")], "meta": {}}
            )
        )
        async with httpx.AsyncClient() as http:
            svc = XService(
                account_id=st.account_id,
                mode="owned",
                oembed=OEmbedReader(http),
                api=XApiV2(http, token, clock=st.clock, sleep=FakeSleep()),
                budget=FakeBudgetGuard(limit=1_000_000),
                uow_factory=st.b.uow,
                clock=st.clock,
                own_handle="me",
                own_user_id=OWN_USER_ID,
            )

            async def x_for(_: UUID) -> Any:
                return svc

            sync = OwnSync(
                uow_factory=st.b.uow,
                clock=st.clock,
                x_for=x_for,
                queue=st.queue,
                matcher=None,
                brain=None,
                notifier=st.notifier,
            )
            res = await sync.sync_own_posts(st.account_id)
    assert res.new_posts == 2
    rows = await st.rows(
        "SELECT endpoint, resources, cost_micro_usd FROM x_api_calls WHERE account_id = :a",
        a=st.account_id,
    )
    assert [tuple(r) for r in rows] == [(EP_USER_POSTS, 2, 2_000)]  # $0.001 за свой пост


async def test_sync_skipped_in_mode_off(st: AStand) -> None:
    await st.set_account(x_read_mode="off")
    st.own_post("Пост", post_id="101")
    res = await st.sync.sync_own_posts(st.account_id)
    assert (res.new_posts, res.skipped) == (0, "mode_off")
    assert st.x.calls == []
    assert await st.count("sources", "account_id = :a", a=st.account_id) == 0


async def test_checkpoints_scheduled_at_1h_24h_72h_7d(st: AStand) -> None:
    now = st.clock.now()
    ids = await st.sync.schedule_checkpoints(st.account_id, "555", now)
    assert len(ids) == 4
    await st.sync.schedule_checkpoints(st.account_id, "555", now)  # повтор — без дублей
    jobs = await st.jobs("analytics.snapshot")
    got = [(j.payload["x_post_id"], j.payload["checkpoint_min"], j.run_at) for j in jobs]
    assert got == [
        ("555", 60, now + timedelta(hours=1)),
        ("555", 1440, now + timedelta(hours=24)),
        ("555", 4320, now + timedelta(hours=72)),
        ("555", 10080, now + timedelta(days=7)),
    ]


async def test_snapshot_age_minutes_computed(st: AStand) -> None:
    st.own_post("Пост", age=timedelta(minutes=90), post_id="301", views=700)
    await st.sync.sync_own_posts(st.account_id)
    st.clock.advance(3600)  # прошёл ещё час: возраст 150 минут
    res = await st.sync.take_snapshot(st.account_id, "301", 60)
    assert res.taken
    rows = await st.rows(
        "SELECT age_minutes, impressions FROM post_metric_snapshots"
        " WHERE account_id = :a AND x_post_id = '301' ORDER BY ts",
        a=st.account_id,
    )
    assert [tuple(r) for r in rows] == [(90, 700), (150, 700)]
    assert await st.count("material_signals", "origin = 'own_sync'") == 2


async def test_own_posts_become_voice_sources_once(st: AStand) -> None:
    from sqlalchemy import func, select

    from xcopilot.db.models import Source
    from xcopilot.db.repos.sources import own_voice

    # история уже импортирована: её посты не подтягиваются повторно
    for i in range(1, 4):
        st.own_post(f"Старый пост номер {i}", age=timedelta(days=5 + i), post_id=str(100 + i))
    await st.b.importer.import_own_history(st.account_id, limit=10)
    st.own_post("Новый пост про рынок", post_id="200")
    await st.sync.sync_own_posts(st.account_id)
    await st.sync.sync_own_posts(st.account_id)
    async with st.sf() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(Source)
                .where(Source.account_id == st.account_id, own_voice(), Source.external_id == "200")
            )
        ).scalar_one()
    assert n == 1
    assert await st.count("sources", "account_id = :a", a=st.account_id) == 4
    assert len(await st.jobs("ingest.process")) >= 1


async def test_bot_assisted_flag_from_publication(st: AStand) -> None:
    body = "Пост, который написал бот и одобрил владелец"
    st.own_post(body, post_id="401")
    st.own_post("Пост, который владелец написал сам", post_id="402")
    await st.publish(body, "401", st.clock.now() - timedelta(hours=2))
    await st.sync.sync_own_posts(st.account_id)
    rows = await st.rows(
        "SELECT external_id, meta ->> 'bot_assisted' FROM sources WHERE account_id = :a",
        a=st.account_id,
    )
    assert {tuple(r) for r in rows} == {("401", "true"), ("402", "false")}


class _SpyMatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, list[str]]] = []

    async def match_publications(self, account_id: UUID, own_posts: list[XPost]) -> list[Any]:
        self.calls.append((account_id, [p.id for p in own_posts]))
        return []


async def test_matcher_runs_after_sync(st: AStand) -> None:
    spy = _SpyMatcher()
    sync = OwnSync(
        uow_factory=st.b.uow,
        clock=st.clock,
        x_for=st.sync_x_for(),
        queue=st.queue,
        matcher=spy,  # type: ignore[arg-type]
        brain=None,
        notifier=st.notifier,
    )
    st.own_post("Пост", post_id="501")
    await sync.sync_own_posts(st.account_id)
    assert spy.calls == [(st.account_id, ["501"])]


async def test_token_refresh_failure_pauses_sync_and_notifies(st: AStand) -> None:
    async def broken(_: UUID) -> Any:
        raise XAuthError("токен не обновился")

    notifier = OwnerNotifier()
    sync = OwnSync(
        uow_factory=st.b.uow,
        clock=st.clock,
        x_for=broken,
        queue=st.queue,
        matcher=None,
        brain=None,
        notifier=notifier,
    )
    res = await sync.sync_own_posts(st.account_id)
    assert (res.new_posts, res.skipped) == (0, "auth")
    assert notifier.owner_texts and "X" in notifier.owner_texts[0]
    paused = await st.rows(
        "SELECT settings ->> 'x_sync_paused' FROM accounts WHERE id = :a", a=st.account_id
    )
    assert paused[0][0] == "true"
    st.own_post("Пост", post_id="601")
    again = await st.sync.sync_own_posts(st.account_id)  # на паузе X не читается
    assert (again.new_posts, again.skipped) == (0, "paused")
    assert st.x.calls == []


def _job(st: AStand, kind: str, payload: dict[str, Any]) -> Any:
    from xcopilot.jobs.queue import Job

    return Job(
        id=st.account_id, account_id=st.account_id, kind=kind, payload=payload, status="running",
        attempts=1, max_attempts=5, run_at=st.clock.now(), locked_by="t", last_error=None,
    )  # fmt: skip


async def test_job_handlers_run_sync_and_snapshot(st: AStand) -> None:
    from xcopilot.analytics.jobs import handle_analytics_snapshot, handle_analytics_sync

    st.own_post("Пост для задач", age=timedelta(hours=3), post_id="1001")
    assert await handle_analytics_sync(_job(st, "analytics.sync", {}), sync=st.sync) == 1
    payload = {"x_post_id": "1001", "checkpoint_min": 60}
    assert await handle_analytics_snapshot(_job(st, "analytics.snapshot", payload), sync=st.sync)


async def test_history_import_queues_indexing_for_chunks(st: AStand) -> None:
    """Своя история ложится в куски (паспорт, ретривал, дрейф голоса): ingest.process на пост."""
    for i in range(3):
        st.own_post(f"Старый пост {i}", age=timedelta(days=5 + i), post_id=str(100 + i))
    await st.b.importer.import_own_history(st.account_id, limit=10)
    assert len(await st.jobs("ingest.process")) == 3
