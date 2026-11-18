"""Короткие ссылки t.co: раскрытие по редиректу без живой сети, «пост — только ссылка»."""

from __future__ import annotations

import httpx

from xcopilot.x.shortlinks import expand_short_link, find_short_link, only_short_links

TCO = "https://t.co/SjXqlQtnS9"
FINAL = "https://twitter.com/boldleonidas/status/2123053023652176342/photo/1"


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_expand_follows_tco_redirect() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": FINAL})

    async with _client(httpx.MockTransport(handle)) as http:
        assert await expand_short_link(http, TCO) == FINAL
    assert seen == [TCO]  # на сам x.com не ходим: адрес уже ведёт к посту


async def test_expand_failure_returns_none() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    async with _client(httpx.MockTransport(handle)) as http:
        assert await expand_short_link(http, TCO) is None


async def test_expand_without_redirect_returns_none() -> None:
    async with _client(httpx.MockTransport(lambda r: httpx.Response(200))) as http:
        assert await expand_short_link(http, TCO) is None


async def test_expand_refuses_other_hosts() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(301, headers={"location": FINAL})

    async with _client(httpx.MockTransport(handle)) as http:
        assert await expand_short_link(http, "https://bit.ly/abc") is None
        assert await expand_short_link(http, "http://127.0.0.1/x") is None
    assert calls == []


def test_find_short_link() -> None:
    assert find_short_link(f"глянь {TCO} пожалуйста") == TCO
    assert find_short_link("t.co/AbC123") == "https://t.co/AbC123"
    assert find_short_link("https://x.com/a/status/1") is None


def test_only_short_links() -> None:
    assert only_short_links(TCO)
    assert only_short_links(f"  {TCO}\n https://t.co/zzz ")
    assert not only_short_links(f"Смотрите график {TCO}")
    assert not only_short_links("")
