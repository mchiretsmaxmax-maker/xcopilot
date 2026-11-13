"""Этап 6: клиент X API v2 — только GET, лимиты частоты, постраничный обход, метрики."""

import ast
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import pytest
import respx

import xcopilot.x.api_v2 as api_module
from tests.fakes import FakeClock, FakeSleep
from xcopilot.x.api_v2 import API_BASE, EP_USER_POSTS, XApiV2
from xcopilot.x.errors import XNotFound
from xcopilot.x.models import XMetrics

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
USER = "1500000000000000000"


async def _token() -> str:
    return "test-access-token"


def _api(http: httpx.AsyncClient, clock: FakeClock, sleep: FakeSleep) -> XApiV2:
    return XApiV2(http, _token, clock=clock, sleep=sleep)


def _tweet(id_: str, **extra: object) -> dict[str, object]:
    return {
        "id": id_,
        "text": f"post {id_}",
        "author_id": USER,
        "created_at": "2026-11-12T09:00:00.000Z",
        "edit_history_tweet_ids": [id_],
        "public_metrics": {
            "retweet_count": 1,
            "reply_count": 2,
            "like_count": 3,
            "quote_count": 4,
            "bookmark_count": 5,
            "impression_count": 600,
        },
        **extra,
    }


class RecordingMeter:
    def __init__(self) -> None:
        self.before_calls: list[tuple[str, list[str] | None, int]] = []
        self.after_calls: list[tuple[str, list[str], int | None, bool]] = []

    async def before(
        self, endpoint: str, resource_ids: Sequence[str] | None, max_resources: int
    ) -> None:
        ids = None if resource_ids is None else list(resource_ids)
        self.before_calls.append((endpoint, ids, max_resources))

    async def after(
        self, endpoint: str, resource_ids: Sequence[str], http_status: int | None, ok: bool
    ) -> None:
        self.after_calls.append((endpoint, list(resource_ids), http_status, ok))


async def test_rate_limit_waits_until_reset() -> None:
    clock, sleep = FakeClock(T0), FakeSleep()
    reset = int(T0.timestamp()) + 30
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get("/2/tweets/42").mock(
            side_effect=[
                httpx.Response(
                    429,
                    headers={"x-rate-limit-remaining": "0", "x-rate-limit-reset": str(reset)},
                    json={"title": "Too Many Requests", "status": 429},
                ),
                httpx.Response(200, json={"data": _tweet("42")}),
            ]
        )
        async with httpx.AsyncClient() as http:
            meter = RecordingMeter()
            post = await _api(http, clock, sleep).get_post("42", meter=meter)
    assert post.id == "42"
    assert route.call_count == 2
    assert len(sleep.waits) == 1
    assert 30 <= sleep.waits[0] <= 31
    # 429 тоже попадает в журнал (без ресурсов), успешный повтор — с ресурсом
    assert meter.after_calls == [
        ("GET /2/tweets/:id", [], 429, False),
        ("GET /2/tweets/:id", ["42"], 200, True),
    ]
    auth = route.calls.last.request.headers["authorization"]
    assert auth == "Bearer test-access-token"


async def test_own_posts_paginate_with_since_id() -> None:
    clock, sleep = FakeClock(T0), FakeSleep()
    pages = [
        httpx.Response(
            200,
            json={
                "data": [_tweet("105"), _tweet("104")],
                "meta": {"result_count": 2, "next_token": "page2"},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [_tweet("103"), _tweet("102")],
                "meta": {"result_count": 2, "next_token": "page3"},
            },
        ),
        httpx.Response(200, json={"data": [_tweet("101")], "meta": {"result_count": 1}}),
    ]
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get(f"/2/users/{USER}/tweets").mock(side_effect=pages)
        async with httpx.AsyncClient() as http:
            meter = RecordingMeter()
            posts = await _api(http, clock, sleep).get_own_posts(
                USER, since_id="100", limit=100, meter=meter
            )
    assert [p.id for p in posts] == ["105", "104", "103", "102", "101"]
    assert route.call_count == 3
    params = [c.request.url.params for c in route.calls]
    assert all(p["since_id"] == "100" for p in params)
    assert "pagination_token" not in params[0]
    assert params[1]["pagination_token"] == "page2"
    assert params[2]["pagination_token"] == "page3"
    assert all("non_public_metrics" in p["tweet.fields"] for p in params)
    assert [a[1] for a in meter.after_calls] == [["105", "104"], ["103", "102"], ["101"]]
    assert all(b[0] == EP_USER_POSTS for b in meter.before_calls)


async def test_own_posts_stop_at_limit() -> None:
    clock, sleep = FakeClock(T0), FakeSleep()
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get(f"/2/users/{USER}/tweets").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [_tweet(str(i)) for i in range(20, 10, -1)],
                    "meta": {"result_count": 10, "next_token": "more"},
                },
            )
        )
        async with httpx.AsyncClient() as http:
            posts = await _api(http, clock, sleep).get_own_posts(USER, limit=7)
    assert len(posts) == 7
    assert route.call_count == 1
    assert route.calls.last.request.url.params["max_results"] == "7"
    assert "since_id" not in route.calls.last.request.url.params


async def test_metrics_mapping_public_and_non_public() -> None:
    clock, sleep = FakeClock(T0), FakeSleep()
    fresh = _tweet(
        "2",
        non_public_metrics={
            "impression_count": 650,
            "url_link_clicks": 7,
            "user_profile_clicks": 11,
        },
    )
    old = _tweet("1")
    with respx.mock(base_url=API_BASE) as mock:
        mock.get(f"/2/users/{USER}/tweets").mock(
            return_value=httpx.Response(200, json={"data": [fresh, old], "meta": {}})
        )
        async with httpx.AsyncClient() as http:
            posts = await _api(http, clock, sleep).get_own_posts(USER)
    by_id = {p.id: p for p in posts}
    assert by_id["2"].metrics == XMetrics(
        likes=3,
        reposts=1,
        replies=2,
        quotes=4,
        bookmarks=5,
        impressions=650,
        url_clicks=7,
        profile_clicks=11,
    )
    assert by_id["1"].metrics == XMetrics(
        likes=3,
        reposts=1,
        replies=2,
        quotes=4,
        bookmarks=5,
        impressions=600,
        url_clicks=None,
        profile_clicks=None,
    )
    assert by_id["2"].created_at == datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    assert by_id["2"].source == "api"
    assert by_id["2"].author_id == USER


async def test_get_post_not_found_is_typed() -> None:
    clock, sleep = FakeClock(T0), FakeSleep()
    body = {
        "errors": [
            {
                "value": "9",
                "detail": "Could not find tweet with id: [9].",
                "title": "Not Found Error",
                "type": "https://api.twitter.com/2/problems/resource-not-found",
            }
        ]
    }
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/tweets/9").mock(return_value=httpx.Response(200, json=body))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XNotFound):
                await _api(http, clock, sleep).get_post("9")


def test_client_has_no_write_methods() -> None:
    source = inspect.getsource(api_module)
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.lower() in write_verbs:
            problems.append(f"вызов .{node.attr}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        ):
            problems.append(f"метод {node.value}")
    assert problems == []
    public = [n for n, _ in inspect.getmembers(XApiV2, inspect.iscoroutinefunction)]
    assert public and all(n.startswith(("get_", "_")) for n in public)
    assert "oauth2/token" not in source
