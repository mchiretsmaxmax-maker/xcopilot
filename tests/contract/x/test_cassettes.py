"""Этап 6: разбор ответов X API v2 по кассетам (перезаписать на настоящих ключах)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import respx

from tests.fakes import FakeClock, FakeSleep
from xcopilot.x.api_v2 import API_BASE, XApiV2
from xcopilot.x.models import XMetrics

CASSETTES = Path(__file__).resolve().parents[2] / "fixtures" / "x" / "cassettes"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CASSETTES / name).read_text())
    return data


def _response(cassette: dict[str, Any]) -> httpx.Response:
    resp = cassette["response"]
    return httpx.Response(resp["status"], headers=resp["headers"], json=resp["json"])


async def _token() -> str:
    return "test-access-token"


async def test_contract_get_post_cassette() -> None:
    cassette = _load("get_post.json")
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get(cassette["request"]["path"]).mock(return_value=_response(cassette))
        async with httpx.AsyncClient() as http:
            api = XApiV2(http, _token, clock=FakeClock(), sleep=FakeSleep())
            post = await api.get_post("1855000000000000001")
    params = route.calls.last.request.url.params
    fields = set(params["tweet.fields"].split(","))
    assert {"created_at", "public_metrics", "note_tweet", "author_id"} <= fields
    assert "non_public_metrics" not in fields  # у чужих постов их нет — запрос упал бы
    assert params["expansions"] == "author_id"
    assert post.id == "1855000000000000001"
    assert post.text.startswith("Shipping the new parser today.")
    assert "Long version" in post.text  # длинный пост — из note_tweet
    assert post.author_handle == "devexample"
    assert post.author_name == "Dev Example"
    assert post.author_id == "2244994945"
    assert post.created_at == datetime(2026, 11, 10, 14, 3, 22, tzinfo=UTC)
    assert post.metrics == XMetrics(
        likes=210, reposts=12, replies=4, quotes=3, bookmarks=9, impressions=15400
    )
    assert post.possibly_truncated is False
    assert post.source == "api"


async def test_contract_own_timeline_cassette() -> None:
    p1, p2 = _load("own_timeline_p1.json"), _load("own_timeline_p2.json")
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get(p1["request"]["path"]).mock(side_effect=[_response(p1), _response(p2)])
        async with httpx.AsyncClient() as http:
            api = XApiV2(http, _token, clock=FakeClock(), sleep=FakeSleep())
            posts = await api.get_own_posts("1500000000000000000", since_id="1854000000000000000")
    assert route.call_count == 2
    second = route.calls[1].request.url.params
    assert second["pagination_token"] == p2["request"]["params"]["pagination_token"]
    assert second["since_id"] == "1854000000000000000"
    assert [p.id for p in posts] == [
        "1855100000000000003",
        "1855100000000000002",
        "1855100000000000001",
    ]
    newest, reply, old = posts
    assert newest.metrics == XMetrics(
        likes=30,
        reposts=2,
        replies=1,
        quotes=0,
        bookmarks=4,
        impressions=1250,
        url_clicks=7,
        profile_clicks=11,
    )
    assert reply.in_reply_to_id == "1855000000000000777"
    assert reply.conversation_id == "1855000000000000777"
    assert old.metrics is not None
    assert old.metrics.impressions == 900
    assert old.metrics.url_clicks is None
    assert old.text.startswith("Первый пост")
