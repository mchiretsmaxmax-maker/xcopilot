"""Безопасная загрузка страниц по ссылке владельца (ТЗ v3.1, этап 7): `safe_get`.

Защита от SSRF: только http/https без логина в адресе; имя резолвится заранее, и все его
адреса должны быть публичными (не localhost, не частные сети, не link-local/метаданные
облака, не multicast/reserved). Соединение идёт на проверенный IP (Host и SNI — исходное
имя), поэтому подмена DNS между проверкой и запросом не помогает. Редиректы — вручную,
каждый адрес проверяется заново. Тело читается потоком и обрывается на пределе.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from xcopilot.ingest.errors import ParseError

Resolver = Callable[[str], Awaitable[list[str]]]
Getter = Callable[[str, int], Awaitable["Fetched"]]

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "Mozilla/5.0 (compatible; xcopilot-reader/1.0)"
_IP = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes


class FetchFailed(ParseError):
    """Страница не загрузилась."""


class BlockedAddress(FetchFailed):
    """Адрес запрещён: не http(s), внутренняя сеть, localhost."""


class TooLarge(FetchFailed):
    """Страница больше предела."""


async def system_resolver(host: str) -> list[str]:
    """Все адреса имени через системный DNS (без кэша)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise FetchFailed("Не нашёл такой сайт. Проверь ссылку.") from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public(ip: _IP) -> bool:
    """Адрес из публичного интернета (IPv4-в-IPv6 проверяется как IPv4)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _blocked() -> BlockedAddress:
    return BlockedAddress("Эту ссылку открыть нельзя: нужен обычный сайт в интернете.")


async def _pinned_ip(url: httpx.URL, resolver: Resolver) -> str:
    """Проверенный публичный IP для адреса или BlockedAddress."""
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise _blocked()
    host = url.host
    try:
        literal: _IP | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        resolved = await resolver(host)
        try:
            addresses = [ipaddress.ip_address(a.split("%", 1)[0]) for a in resolved]
        except ValueError:
            raise _blocked() from None
    if not addresses or not all(is_public(a) for a in addresses):
        raise _blocked()
    return str(addresses[0])


async def safe_get(
    url: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 10.0,
    max_redirects: int = 3,
) -> Fetched:
    """Загрузить страницу по публичному адресу; не больше `max_bytes` байт."""
    resolve = resolver or system_resolver
    try:
        current = httpx.URL(url.strip())
    except httpx.InvalidURL:
        raise _blocked() from None
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout_s, follow_redirects=False, trust_env=False
    ) as client:
        for _ in range(max_redirects + 1):
            ip = await _pinned_ip(current, resolve)
            target = current.copy_with(host=ip)
            host_header = current.netloc.decode("ascii")
            extensions = {"sni_hostname": current.host} if current.scheme == "https" else {}
            request = client.build_request(
                "GET",
                target,
                headers={"Host": host_header, "User-Agent": USER_AGENT, "Accept": "text/html"},
                extensions=extensions,
            )
            try:
                response = await client.send(request, stream=True)
            except httpx.HTTPError:
                raise FetchFailed("Сайт не ответил. Попробуй позже или пришли текст.") from None
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchFailed("Сайт ответил странным редиректом.")
                    current = current.join(location)
                    continue
                return await _read(current, response, max_bytes)
            finally:
                await response.aclose()
    raise FetchFailed("Слишком много редиректов.")


async def _read(url: httpx.URL, response: httpx.Response, max_bytes: int) -> Fetched:
    if response.status_code >= 400:
        raise FetchFailed(f"Сайт ответил ошибкой {response.status_code}.")
    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.lower().startswith(ALLOWED_TYPES):
        raise FetchFailed("По ссылке не страница с текстом.")
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise _too_large(max_bytes)
    body = bytearray()
    async for part in response.aiter_bytes():
        body += part
        if len(body) > max_bytes:
            raise _too_large(max_bytes)
    return Fetched(
        url=str(url), status=response.status_code, content_type=content_type, body=bytes(body)
    )


def _too_large(max_bytes: int) -> TooLarge:
    return TooLarge(f"Страница больше {max_bytes // (1024 * 1024)} МБ — пришли текст статьи.")
