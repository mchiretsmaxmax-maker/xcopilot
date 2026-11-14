"""Этап 7, дельта v3.1: статья по ссылке — безопасная загрузка и основной текст."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest

from tests.fakes.screens import article_html
from tests.samples import unique_text
from xcopilot.ingest.net import (
    BlockedAddress,
    Fetched,
    FetchFailed,
    TooLarge,
    safe_get,
    system_resolver,
)
from xcopilot.ingest.parsers.article import MAX_ARTICLE_BYTES, ArticleEmpty, fetch_article

PUBLIC = "93.184.216.34"


def resolver(table: dict[str, list[str]]) -> Callable[[str], Awaitable[list[str]]]:
    async def resolve(host: str) -> list[str]:
        return table.get(host, [PUBLIC])

    return resolve


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_article_fetch_extracts_main_text() -> None:
    paras = [unique_text(i, words=40) + "." for i in range(6)]
    html = article_html("Как писать треды", paras)

    async def get(url: str, max_bytes: int) -> Fetched:
        assert max_bytes == MAX_ARTICLE_BYTES == 2 * 1024 * 1024
        return Fetched(url=url, status=200, content_type="text/html", body=html.encode())

    doc = await fetch_article("https://blog.example.com/a", get=get)
    for p in paras:
        assert p[:60] in doc.text
    for junk in ("Купите курс", "Все права защищены", "О нас"):
        assert junk not in doc.text
    assert doc.meta["title"] == "Как писать треды"
    assert doc.meta["words"] >= 200
    assert doc.meta["url"] == "https://blog.example.com/a"

    # и через настоящий safe_get: запрос идёт на проверенный адрес, Host — исходный
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    fetched = await safe_get(
        "https://blog.example.com/a",
        resolver=resolver({"blog.example.com": [PUBLIC]}),
        transport=transport(handler),
    )
    assert fetched.body.decode() == html
    assert seen[0].url.host == PUBLIC
    assert seen[0].headers["host"] == "blog.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://internal.example.com/",  # резолвится во внутренний адрес
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://user:pass@example.com/",
    ],
)
async def test_article_fetch_blocks_private_addresses(url: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="secret")

    with pytest.raises(BlockedAddress):
        await safe_get(
            url,
            resolver=resolver({"internal.example.com": ["10.1.2.3"], "localhost": ["127.0.0.1"]}),
            transport=transport(handler),
        )
    assert calls == []


async def test_article_fetch_blocks_redirect_to_private_address() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(BlockedAddress):
        await safe_get(
            "https://blog.example.com/a",
            resolver=resolver({}),
            transport=transport(handler),
        )
    assert len(calls) == 1


async def test_article_over_limit_rejected() -> None:
    big = b"<p>" + b"a" * (3 * 1024 * 1024) + b"</p>"

    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=big)

    with pytest.raises(TooLarge):
        await safe_get(
            "https://example.com/big", resolver=resolver({}), transport=transport(declared)
        )

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(0, len(big), 65536):
            yield big[i : i + 65536]

    def streamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())

    with pytest.raises(TooLarge):
        await safe_get(
            "https://example.com/big", resolver=resolver({}), transport=transport(streamed)
        )


async def test_safe_get_follows_public_redirect_and_checks_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        if request.url.path == "/new":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")
        if request.url.path == "/pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%")
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if request.url.path == "/nolocation":
            return httpx.Response(302)
        return httpx.Response(404)

    kw = {"resolver": resolver({}), "transport": transport(handler)}
    fetched = await safe_get("https://example.com/old", **kw)  # type: ignore[arg-type]
    assert fetched.url == "https://example.com/new" and fetched.body == b"<p>ok</p>"
    for path in ("/pdf", "/loop", "/nolocation", "/missing"):
        with pytest.raises(FetchFailed):
            await safe_get(f"https://example.com{path}", **kw)  # type: ignore[arg-type]


async def test_safe_get_network_error_and_bad_input() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(FetchFailed):
        await safe_get("https://example.com/", resolver=resolver({}), transport=transport(boom))
    with pytest.raises(BlockedAddress):
        await safe_get("http://[::ffff:127.0.0.1]/", resolver=resolver({}))
    with pytest.raises(BlockedAddress):
        await safe_get("http://bad.example/", resolver=resolver({"bad.example": ["not-an-ip"]}))
    with pytest.raises(BlockedAddress):
        await safe_get("http://empty.example/", resolver=resolver({"empty.example": []}))


async def test_system_resolver_blocks_localhost_and_reports_unknown_host() -> None:
    assert "127.0.0.1" in await system_resolver("localhost")
    with pytest.raises(BlockedAddress):
        await safe_get("http://localhost/")
    with pytest.raises(FetchFailed):
        await system_resolver("no-such-host.invalid")


async def test_fetch_article_charset_and_empty_page() -> None:
    html = article_html("Заголовок", [unique_text(i, words=40) + "." for i in range(5)])

    async def get(url: str, max_bytes: int) -> Fetched:
        return Fetched(url, 200, "text/html; charset=cp1251", html.encode("cp1251"))

    doc = await fetch_article("https://e.com/a", get=get)
    assert doc.meta["title"] == "Заголовок"

    async def empty(url: str, max_bytes: int) -> Fetched:
        return Fetched(url, 200, "text/html; charset=nonsense", b"<html><body></body></html>")

    with pytest.raises(ArticleEmpty):
        await fetch_article("https://e.com/b", get=empty)
