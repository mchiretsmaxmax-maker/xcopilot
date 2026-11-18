"""R1 (ADR 0034, п. 2): поля ответа, общий ограничитель 300 запросов/мин, 429 → XUnavailable.

`lang`/`in_reply_to_id` у ответа из кассеты уже проверяет test_fxembed_v2
(test_fetch_replies_top_by_likes_without_media_only); здесь — недостающее.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.unit.x.test_fxembed_v2 import NOTE_ID, Sleeps, cassette
from xcopilot.x.errors import XUnavailable
from xcopilot.x.public_read import FX_V2, PublicPostReader, parse_status


def test_reply_post_has_lang_and_replying_to() -> None:
    obj = cassette("conversation_p1.json")["replies"][0]
    post = parse_status(obj, strip_mentions=True)
    assert post is not None
    assert post.lang == "en" and post.in_reply_to_id == NOTE_ID


def test_reply_missing_lang_is_none() -> None:
    obj = dict(cassette("conversation_p1.json")["replies"][0])
    obj.pop("lang", None)
    post = parse_status(obj, strip_mentions=True)
    assert post is not None and post.lang is None


async def test_rate_limiter_waits_after_limit() -> None:
    sleeps = Sleeps()
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/profile/NASA").mock(
            return_value=httpx.Response(200, json=cassette("profile_nasa.json"))
        )
        async with httpx.AsyncClient() as http:
            reader = PublicPostReader(
                http, sleep=sleeps.sleep, clock=sleeps.clock, min_interval=0.0, per_minute=300
            )
            for _ in range(300):
                await reader.fetch_profile("NASA")
            assert sleeps.calls == []
            await reader.fetch_profile("NASA")
    assert len(sleeps.calls) == 1 and sleeps.calls[0] == pytest.approx(60.0)


async def test_429_raises_unavailable() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XUnavailable):
                await PublicPostReader(http).fetch_replies(NOTE_ID)
