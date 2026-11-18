"""Чтение публичного поста без ключа: oEmbed, затем fxtwitter (ADR 0031)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.fakes.clock import FakeClock
from tests.fakes.x import FakeBudgetGuard
from xcopilot.x.client import XService
from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef
from xcopilot.x.oembed import OEMBED_URL, OEmbedReader
from xcopilot.x.public_read import FXTWITTER_URL, PublicPostReader
from xcopilot.x.urls import parse_post_url

REF = PostRef("1855000000000000001", "devexample")
FX = f"{FXTWITTER_URL}/2/status/1855000000000000001"  # FxEmbed v2: без хэндла
FULL = ("Полный текст поста. " * 30).strip()


def _oembed(text: str) -> dict[str, object]:
    return {
        "author_name": "Dev Example",
        "author_url": "https://twitter.com/devexample",
        "html": (
            f'<blockquote class="twitter-tweet"><p lang="ru">{text}</p>&mdash; Dev (@devexample) '
            '<a href="https://twitter.com/devexample/status/1855000000000000001">'
            "November 10, 2026</a></blockquote>"
        ),
    }


def _fx(text: str = FULL) -> dict[str, object]:
    return {
        "code": 200,
        "message": "OK",
        "tweet": {
            "id": "1855000000000000001",
            "text": text,
            "author": {"screen_name": "DevExample", "name": "Dev Example", "id": "42"},
            "likes": 7,
            "retweets": 3,
            "replies": 2,
            "created_timestamp": 1762783402,
        },
    }


def _reader(http: httpx.AsyncClient) -> PublicPostReader:
    return PublicPostReader(http)


async def test_oembed_ok_no_fxtwitter_call() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("Привет мир")))
        fx = mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.text == "Привет мир"
    assert post.source == "oembed"
    assert post.author_handle == "devexample"
    # ADR 0033: текст остаётся от oEmbed, цифры для автоисхода — одним запросом к fxtwitter
    assert post.metrics is not None and post.metrics.likes == 7
    assert fx.call_count == 1


async def test_empty_oembed_falls_back_to_fxtwitter() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("")))
        mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.text == FULL
    assert post.source == "fxtwitter"
    assert post.author_handle == "DevExample"
    assert post.author_name == "Dev Example"
    assert post.metrics is not None
    assert (post.metrics.likes, post.metrics.reposts, post.metrics.replies) == (7, 3, 2)
    assert post.possibly_truncated is False


async def test_truncated_oembed_falls_back_to_fxtwitter() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("Обрыв…")))
        mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.text == FULL


async def test_truncated_oembed_kept_when_fxtwitter_down() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("Обрыв…")))
        mock.get(FX).mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.source == "oembed"
    assert post.possibly_truncated is True


async def test_oembed_error_uses_fxtwitter() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(403))
        mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.source == "fxtwitter"


async def test_both_say_not_found() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(404))
        mock.get(FX).mock(
            return_value=httpx.Response(404, json={"code": 404, "message": "NOT_FOUND"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(XNotFound):
                await _reader(http).get_post(REF)


async def test_both_down_is_unavailable() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(503))
        mock.get(FX).mock(return_value=httpx.Response(502))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XUnavailable):
                await _reader(http).get_post(REF)


async def test_network_error_retried_once() -> None:
    async with respx.mock() as mock:
        route = mock.get(OEMBED_URL).mock(
            side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=_oembed("Ок"))]
        )
        mock.get(FX).mock(return_value=httpx.Response(503))  # цифры недоступны — пост без них
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(REF)
    assert post.text == "Ок"
    assert route.call_count == 2


async def test_ref_without_handle_uses_i_path() -> None:
    ref = PostRef("1855000000000000001")
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("")))
        route = mock.get(f"{FXTWITTER_URL}/2/status/1855000000000000001").mock(
            return_value=httpx.Response(200, json=_fx())
        )
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(ref)
    assert route.call_count == 1
    assert post.author_handle == "DevExample"


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/devexample/status/1855000000000000001?s=20&t=abc",
        "https://mobile.twitter.com/devexample/status/1855000000000000001",
        "twitter.com/devexample/status/1855000000000000001/photo/1",
    ],
)
def test_link_variants_normalize(url: str) -> None:
    ref = parse_post_url(url)
    assert ref == REF
    assert ref.url == "https://x.com/devexample/status/1855000000000000001"


async def test_service_off_mode_reads_public_without_error() -> None:
    def _no_uow() -> object:
        raise AssertionError("БД не нужна в режиме off")

    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("")))
        mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            svc = XService(
                account_id=__import__("uuid").uuid4(),
                mode="off",
                oembed=PublicPostReader(http),
                api=None,
                budget=FakeBudgetGuard(),
                uow_factory=_no_uow,  # type: ignore[arg-type]
                clock=FakeClock(),
            )
            post = await svc.get_post(REF)
    assert post.text == FULL and post.source == "fxtwitter"


def test_oembed_reader_still_importable() -> None:
    assert OEmbedReader is not None
