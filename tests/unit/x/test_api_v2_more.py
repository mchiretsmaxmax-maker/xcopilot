"""Этап 6 (доп.): пачка постов, профили, разбор ошибок и лимитов X API v2."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from tests.fakes import FakeClock, FakeSleep
from xcopilot.x.api_v2 import API_BASE, XApiV2, parse_metrics
from xcopilot.x.errors import XAuthError, XNotFound, XUnavailable
from xcopilot.x.models import XUser

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)


async def _token() -> str:
    return "test-access-token"


def _api(http: httpx.AsyncClient, sleep: FakeSleep | None = None) -> XApiV2:
    return XApiV2(http, _token, clock=FakeClock(T0), sleep=sleep or FakeSleep())


def _tweet(id_: str) -> dict[str, object]:
    return {"id": id_, "text": f"t{id_}", "author_id": "7"}


async def test_get_posts_batches_by_100_and_dedupes() -> None:
    ids = [str(i) for i in range(150)] + ["0", "1"]
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get("/2/tweets").mock(
            side_effect=lambda req: httpx.Response(
                200,
                json={
                    "data": [_tweet(i) for i in req.url.params["ids"].split(",")],
                    "includes": {"users": [{"id": "7", "username": "seven", "name": "S"}]},
                },
            )
        )
        async with httpx.AsyncClient() as http:
            posts = await _api(http).get_posts(ids)
        assert route.call_count == 2
        sizes = [len(c.request.url.params["ids"].split(",")) for c in route.calls]
    assert sizes == [100, 50]
    assert len(posts) == 150
    assert posts[0].author_handle == "seven"
    assert posts[0].metrics is None


async def test_get_user_and_me() -> None:
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/users/by/username/devexample").mock(
            return_value=httpx.Response(
                200, json={"data": {"id": "1", "username": "devexample", "name": "Dev"}}
            )
        )
        mock.get("/2/users/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "2", "username": "me"}})
        )
        mock.get("/2/users/by/username/ghost").mock(
            return_value=httpx.Response(200, json={"errors": [{"title": "Not Found Error"}]})
        )
        async with httpx.AsyncClient() as http:
            api = _api(http)
            assert await api.get_user("devexample") == XUser("1", "devexample", "Dev")
            assert await api.get_me() == XUser("2", "me", None)
            with pytest.raises(XNotFound):
                await api.get_user("ghost")


async def test_get_me_without_data_is_auth_error() -> None:
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/users/me").mock(return_value=httpx.Response(200, json={}))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XAuthError):
                await _api(http).get_me()


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, XAuthError), (404, XNotFound), (500, XUnavailable), (403, XUnavailable)],
)
async def test_http_errors_are_typed_and_hide_token(status: int, error: type[Exception]) -> None:
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/tweets/1").mock(return_value=httpx.Response(status, json={}))
        async with httpx.AsyncClient() as http:
            with pytest.raises(error) as exc:
                await _api(http).get_post("1")
    assert "test-access-token" not in str(exc.value)


async def test_transport_error_and_non_json_are_unavailable() -> None:
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/tweets/1").mock(side_effect=httpx.ConnectError("boom"))
        mock.get("/2/tweets/2").mock(return_value=httpx.Response(200, text="<html>"))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XUnavailable):
                await _api(http).get_post("1")
            with pytest.raises(XUnavailable):
                await _api(http).get_post("2")


async def test_rate_limit_gives_up_after_max_waits_and_defaults_without_header() -> None:
    sleep = FakeSleep()
    with respx.mock(base_url=API_BASE) as mock:
        route = mock.get("/2/tweets/1").mock(return_value=httpx.Response(429, json={}))
        async with httpx.AsyncClient() as http:
            api = XApiV2(http, _token, clock=FakeClock(T0), sleep=sleep, max_rate_limit_waits=2)
            with pytest.raises(XUnavailable):
                await api.get_post("1")
        assert route.call_count == 3
    assert sleep.waits == [60.0, 60.0]


async def test_rate_limit_reset_in_past_waits_a_second() -> None:
    sleep = FakeSleep()
    past = str(int(T0.timestamp()) - 100)
    with respx.mock(base_url=API_BASE) as mock:
        mock.get("/2/tweets/1").mock(
            side_effect=[
                httpx.Response(429, headers={"x-rate-limit-reset": past}),
                httpx.Response(429, headers={"x-rate-limit-reset": "soon"}),
                httpx.Response(200, json={"data": _tweet("1")}),
            ]
        )
        async with httpx.AsyncClient() as http:
            post = await _api(http, sleep).get_post("1")
    assert post.id == "1"
    assert sleep.waits == [1.0, 60.0]


def test_parse_metrics_tolerates_garbage() -> None:
    assert parse_metrics({}) is None
    m = parse_metrics({"public_metrics": {"like_count": "x"}, "non_public_metrics": []})
    assert m is not None
    assert (m.likes, m.impressions, m.url_clicks) == (0, None, None)
