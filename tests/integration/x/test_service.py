"""Этап 6: XService — режимы off/owned/paid, учёт стоимости, дедупликация по UTC-дню, бюджет."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from tests.fakes import FakeBudgetGuard, FakeClock, FakeSleep
from tests.integration.x.conftest import OWN_USER_ID
from xcopilot.db.repos import XApiCallsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import BudgetExceeded
from xcopilot.x.api_v2 import API_BASE, EP_POST, EP_USER_POSTS, XApiV2
from xcopilot.x.client import XMode, XService
from xcopilot.x.errors import XModeError
from xcopilot.x.models import PostRef
from xcopilot.x.oembed import OEMBED_URL, OEmbedReader

FOREIGN = PostRef("1855000000000000001", "devexample")

OEMBED_BODY = {
    "author_name": "Dev Example",
    "author_url": "https://twitter.com/devexample",
    "html": (
        '<blockquote class="twitter-tweet"><p lang="ru" dir="ltr">Текст из oEmbed</p>'
        "&mdash; Dev Example (@devexample) "
        '<a href="https://twitter.com/devexample/status/1855000000000000001">'
        "November 10, 2026</a></blockquote>"
    ),
}


def _tweet(id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "text": f"API текст {id_}",
        "author_id": "2244994945",
        "created_at": "2026-11-10T14:03:22.000Z",
        "public_metrics": {
            "retweet_count": 0,
            "reply_count": 0,
            "like_count": 1,
            "quote_count": 0,
            "bookmark_count": 0,
            "impression_count": 10,
        },
    }


async def _token() -> str:
    return "test-access-token"


def _service(
    mode: XMode,
    http: httpx.AsyncClient,
    budget: FakeBudgetGuard,
    uow_factory: Callable[[], UnitOfWork],
    clock: FakeClock,
    account_id: UUID,
) -> XService:
    api = XApiV2(http, _token, clock=clock, sleep=FakeSleep())
    return XService(
        account_id=account_id,
        mode=mode,
        oembed=OEmbedReader(http),
        api=api,
        budget=budget,
        uow_factory=uow_factory,
        clock=clock,
        own_handle="me",
        own_user_id=OWN_USER_ID,
    )


async def _journal(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID
) -> list[tuple[str, int, int, date, int | None, str]]:
    async with uow_factory() as uow:
        rows = await XApiCallsRepo(uow.session, account_id).list()
        rows.sort(key=lambda r: r.ts)
        return [
            (r.endpoint, r.resources, r.cost_micro_usd, r.utc_day, r.http_status, r.status)
            for r in rows
        ]


def _mock() -> respx.MockRouter:
    mock = respx.mock(assert_all_called=False)
    mock.get(OEMBED_URL, name="oembed").mock(return_value=httpx.Response(200, json=OEMBED_BODY))
    mock.get(f"{API_BASE}/2/tweets/{FOREIGN.id}", name="post").mock(
        return_value=httpx.Response(200, json={"data": _tweet(FOREIGN.id)})
    )
    mock.get(f"{API_BASE}/2/users/{OWN_USER_ID}/tweets", name="own").mock(
        return_value=httpx.Response(
            200,
            json={"data": [_tweet("303"), _tweet("302"), _tweet("301")], "meta": {}},
        )
    )
    mock.route(host="api.x.com", name="any_api").mock(return_value=httpx.Response(500))
    return mock


def _api_calls(mock: respx.MockRouter) -> int:
    return sum(len(r.calls) for r in mock.routes if str(r.name) != "oembed")


async def test_mode_off_never_calls_api(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard()
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _service("off", http, budget, uow_factory, xclock, account_id)
            post = await svc.get_post(FOREIGN)
            with pytest.raises(XModeError):
                await svc.get_own_posts()
            with pytest.raises(XModeError):
                await svc.get_user("devexample")
            own = await svc.get_post(PostRef("999", "me"))
    assert post.source == "oembed"
    assert own.source == "oembed"
    assert _api_calls(mock) == 0
    assert budget.checks == []
    assert await _journal(uow_factory, account_id) == []


async def test_mode_owned_reads_foreign_post_via_oembed_only(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard()
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _service("owned", http, budget, uow_factory, xclock, account_id)
            post = await svc.get_post(FOREIGN)
            with pytest.raises(XModeError):
                await svc.get_user("devexample")
    assert post.source == "oembed"
    assert post.text == "Текст из oEmbed"
    assert mock["oembed"].call_count == 1
    assert _api_calls(mock) == 0
    assert await _journal(uow_factory, account_id) == []


async def test_mode_paid_reads_foreign_post_and_records_5000_micro_usd(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _service("paid", http, budget, uow_factory, xclock, account_id)
            post = await svc.get_post(FOREIGN)
    assert post.source == "api"
    assert post.text == f"API текст {FOREIGN.id}"
    assert mock["post"].call_count == 1
    assert mock["oembed"].call_count == 0
    assert budget.checks == [(account_id, "x_api", 5_000)]
    assert await _journal(uow_factory, account_id) == [
        (EP_POST, 1, 5_000, date(2026, 11, 13), 200, "ok")
    ]


async def test_owned_read_costs_1000_micro_usd_per_post(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _service("owned", http, budget, uow_factory, xclock, account_id)
            posts = await svc.get_own_posts(limit=10)
    assert [p.id for p in posts] == ["303", "302", "301"]
    assert mock["own"].call_count == 1
    # до запроса — оценка по верхней границе (limit), в журнал — по факту
    assert budget.checks == [(account_id, "x_api", 10 * 1_000)]
    assert await _journal(uow_factory, account_id) == [
        (EP_USER_POSTS, 3, 3_000, date(2026, 11, 13), 200, "ok")
    ]


async def test_same_post_same_utc_day_counted_once(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            svc = _service("paid", http, budget, uow_factory, xclock, account_id)
            await svc.get_post(FOREIGN)
            xclock.advance(timedelta(hours=11).total_seconds())  # 23:00 UTC того же дня
            await svc.get_post(FOREIGN)
    assert mock["post"].call_count == 2
    journal = await _journal(uow_factory, account_id)
    assert [(r[1], r[2]) for r in journal] == [(1, 5_000), (0, 0)]
    assert sum(r[2] for r in journal) == 5_000


async def test_new_utc_day_counted_again(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000)
    with _mock():
        async with httpx.AsyncClient() as http:
            svc = _service("paid", http, budget, uow_factory, xclock, account_id)
            xclock.advance(timedelta(hours=11, minutes=59).total_seconds())  # 23:59 UTC
            await svc.get_post(FOREIGN)
            xclock.advance(timedelta(minutes=2).total_seconds())  # 00:01 UTC следующего дня
            await svc.get_post(FOREIGN)
    journal = await _journal(uow_factory, account_id)
    assert [(r[2], r[3]) for r in journal] == [
        (5_000, date(2026, 11, 13)),
        (5_000, date(2026, 11, 14)),
    ]


async def test_budget_cap_blocks_x_call(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    budget = FakeBudgetGuard(limit=1_000_000, spent=999_000)
    with _mock() as mock:
        async with httpx.AsyncClient() as http:
            owned = _service("owned", http, budget, uow_factory, xclock, account_id)
            with pytest.raises(BudgetExceeded):
                await owned.get_own_posts(limit=100)
            paid = _service("paid", http, budget, uow_factory, xclock, account_id)
            with pytest.raises(BudgetExceeded):
                await paid.get_user("devexample")
            # пост по ссылке при исчерпанном бюджете — бесплатный oEmbed вместо API
            post = await paid.get_post(FOREIGN)
    assert post.source == "oembed"
    assert _api_calls(mock) == 0
    assert await _journal(uow_factory, account_id) == []
