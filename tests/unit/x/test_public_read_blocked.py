"""fxtwitter за Cloudflare: 403 на клиента по умолчанию — не «пост удалён» (живой случай 17.11)."""

from __future__ import annotations

import httpx
import respx

from tests.unit.x.test_public_read import FX, FULL, REF, _fx, _oembed
from xcopilot.x.oembed import OEMBED_URL
from xcopilot.x.public_read import PublicPostReader


async def test_fxtwitter_403_keeps_truncated_oembed_text() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("Обрыв…")))
        mock.get(FX).mock(return_value=httpx.Response(403, text="Just a moment..."))
        async with httpx.AsyncClient() as http:
            post = await PublicPostReader(http).get_post(REF)
    assert post.source == "oembed"
    assert post.text.startswith("Обрыв")


async def test_fxtwitter_request_has_own_user_agent() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=_oembed("Обрыв…")))
        fx = mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await PublicPostReader(http).get_post(REF)
    assert post.text == FULL
    ua = fx.calls.last.request.headers.get("user-agent", "")
    assert ua and not ua.startswith("python-httpx")
