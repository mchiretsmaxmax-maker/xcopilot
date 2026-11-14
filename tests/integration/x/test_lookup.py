"""Этап 6 (v3.1): режим lookup, лимит x_lookup, подписчики, история, перепроверка."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from tests.fakes import FakeBudgetGuard, FakeClock, FakeSleep
from tests.integration.x.conftest import OWN_USER_ID
from tests.integration.x.test_service import (
    FOREIGN,
    OEMBED_BODY,
    _api_calls,
    _journal,
    _KeepCallsRouter,
    _tweet,
)
from xcopilot.db.repos import AuthorsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.x.api_v2 import API_BASE, EP_USER_POSTS, XApiV2
from xcopilot.x.client import XService
from xcopilot.x.errors import XModeError
from xcopilot.x.models import PostRef
from xcopilot.x.oembed import OEMBED_URL, OEmbedReader

LOOKUP_LIMIT = 500_000
YOUNG = PostRef("1855000000000000777", "devexample")


async def _token() -> str:
    return "test-access-token"


def _svc(
    http: httpx.AsyncClient,
    budget: FakeBudgetGuard,
    uow_factory: Callable[[], UnitOfWork],
    clock: FakeClock,
    account_id: UUID,
    *,
    mode: str = "lookup",
    jobs: JobQueue | None = None,
) -> XService:
    return XService(
        account_id=account_id,
        mode=mode,  # type: ignore[arg-type]
        oembed=OEmbedReader(http),
        api=XApiV2(http, _token, clock=clock, sleep=FakeSleep()),
        budget=budget,
        uow_factory=uow_factory,
        clock=clock,
        own_handle="me",
        own_user_id=OWN_USER_ID,
        jobs=jobs,
    )


def _guard(**kw: int) -> FakeBudgetGuard:
    return FakeBudgetGuard(
        scope_limits={"x_lookup": LOOKUP_LIMIT, "x_api": 1_000_000},
        scope_spent={k: v for k, v in kw.items()},
    )


def _mock() -> respx.MockRouter:
    mock = _KeepCallsRouter(assert_all_called=False)
    mock.get(OEMBED_URL, name="oembed").mock(return_value=httpx.Response(200, json=OEMBED_BODY))
    mock.get(f"{API_BASE}/2/tweets/{FOREIGN.id}", name="post").mock(
        return_value=httpx.Response(200, json={"data": _tweet(FOREIGN.id)})
    )
    young = {**_tweet(YOUNG.id), "created_at": "2026-11-13T10:00:00.000Z"}
    mock.get(f"{API_BASE}/2/tweets/{YOUNG.id}", name="young").mock(
        return_value=httpx.Response(200, json={"data": young})
    )
    mock.get(f"{API_BASE}/2/users/by/username/devexample", name="user").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "2244994945",
                    "username": "DevExample",
                    "name": "Dev Example",
                    "public_metrics": {"followers_count": 1234, "following_count": 5},
                }
            },
        )
    )
    mock.route(host="api.x.com", name="any_api").mock(return_value=httpx.Response(500))
    return mock


async def test_lookup_reads_only_owner_submitted_posts(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = _guard()
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id)
            post = await svc.lookup_post(FOREIGN)
            via_get = await svc.get_post(FOREIGN)
    assert post.source == "api"
    assert post.author_id == "2244994945"
    assert post.metrics is not None and post.metrics.likes == 1
    assert via_get.source == "api"
    assert mock["post"].call_count == 2
    assert budget.checks[0] == (account_id, "x_lookup", 5_000)
    journal = await _journal(uow_factory, account_id)
    assert journal[0][1:3] == (1, 5_000)


async def test_lookup_mode_never_polls_timelines(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = _guard()
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id)
            with pytest.raises(XModeError):
                await svc.get_own_posts()
            with pytest.raises(XModeError):
                async for _ in svc.iter_own_history(10):
                    pass
    assert mock.routes["any_api"].call_count == 0
    assert _api_calls(mock) == 0
    assert budget.checks == []


async def test_lookup_budget_separate_from_owned(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    # общий лимит x_api исчерпан, лимит x_lookup — нет: точечное чтение идёт
    budget = _guard(x_api=1_000_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id)
            post = await svc.lookup_post(FOREIGN)
    assert post.source == "api"
    assert mock["post"].call_count == 1
    assert {scope for _, scope, _ in budget.checks} == {"x_lookup"}


async def test_lookup_falls_back_to_oembed_when_budget_spent(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = _guard(x_lookup=LOOKUP_LIMIT - 1_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id)
            post = await svc.lookup_post(FOREIGN)
    assert post.source == "oembed"
    assert mock["oembed"].call_count == 1
    assert _api_calls(mock) == 0
    assert await _journal(uow_factory, account_id) == []


async def test_author_followers_cached_30_days(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = _guard()
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id)
            first = await svc.get_user("devexample")
            xclock.advance(timedelta(days=29).total_seconds())
            second = await svc.get_user("devexample")
            assert mock["user"].call_count == 1
            xclock.advance(timedelta(days=2).total_seconds())
            await svc.get_user("devexample")
    assert first.followers == 1234
    assert second.followers == 1234
    assert mock["user"].call_count == 2
    async with uow_factory() as uow:
        author = await AuthorsRepo(uow.session, account_id).by_handle("devexample")
    assert author is not None
    assert author.followers == 1234
    assert author.followers_origin == "x_api"
    assert author.followers_observed_at is not None


async def test_own_history_paginates_to_limit_and_costs_owned(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000)

    def pages(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pagination_token")
        start, size, nxt = {None: (1000, 100, "p2"), "p2": (900, 100, "p3"), "p3": (800, 50, None)}[
            token
        ]
        data = [_tweet(str(start - i)) for i in range(size)]
        meta: dict[str, Any] = {"next_token": nxt} if nxt else {}
        return httpx.Response(200, json={"data": data, "meta": meta})

    mock = _KeepCallsRouter(assert_all_called=False)
    mock.get(f"{API_BASE}/2/users/{OWN_USER_ID}/tweets", name="own").mock(side_effect=pages)
    with mock:
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id, mode="owned")
            got = [p async for p in svc.iter_own_history(250)]
    assert len(got) == 250
    assert got[0].id == "1000"
    assert mock["own"].call_count == 3
    journal = await _journal(uow_factory, account_id)
    assert [(r[0], r[1], r[2]) for r in journal] == [
        (EP_USER_POSTS, 100, 100_000),
        (EP_USER_POSTS, 100, 100_000),
        (EP_USER_POSTS, 50, 50_000),
    ]
    import inspect

    assert inspect.signature(svc.iter_own_history).parameters["limit"].default == 500
    assert json.dumps(budget.checks[0][1]) == '"x_api"'


async def test_young_post_scheduled_for_recheck(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = _guard()
    queue = JobQueue(uow_factory, xclock)
    with _mock():
        async with httpx.AsyncClient() as http:
            svc = _svc(http, budget, uow_factory, xclock, account_id, jobs=queue)
            await svc.lookup_post(FOREIGN)  # старый пост — без перепроверки
            await svc.lookup_post(YOUNG)
            await svc.lookup_post(YOUNG)  # повтор не плодит задачи
    assert await queue.claim("w", 10) == []  # run_at ещё впереди
    xclock.advance(timedelta(hours=23).total_seconds())  # 11:00 UTC 14.11 — после created+24h
    jobs = await queue.claim("w", 10)
    assert [j.kind for j in jobs] == ["x.recheck_metrics"]
    assert jobs[0].payload["post_id"] == YOUNG.id
    assert jobs[0].run_at == datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
