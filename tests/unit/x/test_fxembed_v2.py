"""FxEmbed API v2 на записанных ответах (tests/fixtures/x/fxembed, ADR 0031).

Пост (/2/status), тред (/2/thread), ответы (/2/conversation), цитаты (/2/status/{id}/quotes),
профиль (/2/profile). Живой сети нет: respx подставляет кассеты.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef
from xcopilot.x.oembed import OEMBED_URL
from xcopilot.x.public_read import (
    FX_V2,
    PublicPostReader,
    own_thread,
    parse_fxtwitter,
    parse_status,
    read_target,
    thread_text,
)

CASSETTES = Path(__file__).parents[2] / "fixtures" / "x" / "fxembed"
NOTE_ID = "2026084506822730185"  # XDevelopers, длинный пост, 517 ответов, 417 цитат
NAVAL_MID = "1002103627387813888"  # 4-я часть треда naval «How to Get Rich»
NAVAL_ROOT = "1002103360646823936"


def cassette(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CASSETTES / name).read_text())
    return data


class Sleeps:
    def __init__(self) -> None:
        self.now = 0.0
        self.calls: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.now += seconds


def _reader(http: httpx.AsyncClient, sleeps: Sleeps | None = None) -> PublicPostReader:
    s = sleeps or Sleeps()
    return PublicPostReader(http, sleep=s.sleep, clock=s.clock)


# --- /2/status ---


def test_v2_status_keeps_old_fields_and_adds_new() -> None:
    post = parse_fxtwitter(PostRef(NOTE_ID, "XDevelopers"), cassette("status_note.json"))
    assert post.text.startswith("To help address automated reply spam")
    assert post.author_handle == "XDevelopers"
    assert post.author_id == "2244994945"
    assert post.created_at is not None and post.created_at.year == 2026
    m = post.metrics
    assert m is not None
    assert (m.likes, m.reposts, m.replies, m.quotes, m.bookmarks) == (2870, 298, 517, 417, 388)
    assert m.impressions == 1615365
    assert post.author_followers and post.author_followers > 100_000
    assert post.lang == "en"
    assert post.is_note_tweet is True
    assert post.replying_to is None and post.media == ()


def test_v2_status_media_and_replying_to() -> None:
    post = parse_fxtwitter(PostRef("1585841941203537920"), cassette("status_media.json"))
    assert [(x.type, x.url.startswith("https://pbs.twimg.com/")) for x in post.media] == [
        ("photo", True)
    ]
    assert post.replying_to == "elonmusk"
    assert post.in_reply_to_id == "1585841080431321088"
    reply = parse_fxtwitter(PostRef("2026110348722618597"), cassette("status_reply.json"))
    assert reply.replying_to == "XDevelopers" and reply.in_reply_to_id == NOTE_ID


def test_parse_status_skips_non_status() -> None:
    assert parse_status(None) is None
    assert parse_status({"type": "tombstone", "id": "1"}) is None
    assert parse_status("x") is None


def test_parse_status_strips_reply_mentions() -> None:
    obj = cassette("conversation_p1.json")["replies"][0]
    full = parse_status(obj)
    body = parse_status(obj, strip_mentions=True)
    assert full is not None and full.text.startswith("@XDevelopers ")
    assert (
        body is not None
        and body.text == "When can we block or mute entire countries or continents?"
    )


async def test_get_post_uses_v2_status_url() -> None:
    ref = PostRef(NOTE_ID, "XDevelopers")
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(503))
        route = mock.get(f"{FX_V2}/status/{NOTE_ID}").mock(
            return_value=httpx.Response(200, json=cassette("status_note.json"))
        )
        async with httpx.AsyncClient() as http:
            post = await _reader(http).get_post(ref)
    assert route.call_count >= 1
    assert post.source == "fxtwitter" and post.lang == "en"
    ua = route.calls.last.request.headers.get("user-agent", "")
    assert ua == "xcopilot/1.0"


async def test_get_post_v2_404_is_not_found() -> None:
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(404))
        mock.get(f"{FX_V2}/status/1999999999999999999").mock(
            return_value=httpx.Response(404, json=cassette("status_404.json"))
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(XNotFound):
                await _reader(http).get_post(PostRef("1999999999999999999"))


# --- /2/thread ---


def test_own_thread_keeps_only_author_block() -> None:
    parts = [p for p in map(parse_status, cassette("thread_naval_mid.json")["thread"]) if p]
    own = own_thread(NAVAL_MID, parts)
    assert len(own) == 34 and own[0].id == NAVAL_ROOT
    reply = [p for p in map(parse_status, cassette("thread_single.json")["thread"]) if p]
    assert [p.id for p in own_thread(NOTE_ID, reply)] == [NOTE_ID]


def test_own_thread_drops_foreign_ancestors() -> None:
    root = parse_status(cassette("status_note.json")["status"])
    child = parse_status(cassette("status_reply.json")["status"])
    assert root is not None and child is not None
    assert [p.id for p in own_thread(child.id, [root, child])] == [child.id]


def test_thread_text_numbers_and_caps() -> None:
    parts = [p for p in map(parse_status, cassette("thread_naval_mid.json")["thread"]) if p]
    text = thread_text(parts)
    assert text.startswith("1/34 How to Get Rich")
    assert "\n\n4/34 Ignore people playing status games" in text
    short = thread_text(parts, max_chars=300)
    assert len(short) <= 300 and short.startswith("1/34 ")
    assert thread_text(parts[:1]) == parts[0].text  # одиночный пост — без нумерации


async def test_fetch_thread_from_middle_part_gives_whole_thread() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/thread/{NAVAL_MID}").mock(
            return_value=httpx.Response(200, json=cassette("thread_naval_mid.json"))
        )
        async with httpx.AsyncClient() as http:
            parts = await _reader(http).fetch_thread(PostRef(NAVAL_MID, "naval"))
    assert len(parts) == 34 and parts[0].id == NAVAL_ROOT
    assert {p.author_handle for p in parts} == {"naval"}


async def test_fetch_target_joins_thread_text() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/thread/{NAVAL_MID}").mock(
            return_value=httpx.Response(200, json=cassette("thread_naval_mid.json"))
        )
        async with httpx.AsyncClient() as http:
            reader = _reader(http)
            post = await reader.fetch_target(PostRef(NAVAL_MID, "naval"))
            via = await read_target(reader, PostRef(NAVAL_MID, "naval"))
    assert post.id == NAVAL_MID and post.author_handle == "naval"
    assert post.text.startswith("1/34 How to Get Rich")
    assert post.metrics is not None  # цифры — от присланной части
    assert via.text == post.text


async def test_fetch_target_single_post_is_plain_text() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/thread/{NOTE_ID}").mock(
            return_value=httpx.Response(200, json=cassette("thread_single.json"))
        )
        async with httpx.AsyncClient() as http:
            post = await _reader(http).fetch_target(PostRef(NOTE_ID, "XDevelopers"))
    assert post.text.startswith("To help address automated reply spam")
    assert post.metrics is not None and post.metrics.likes > 0


async def test_fetch_target_falls_back_to_get_post_when_thread_down() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/thread/{NOTE_ID}").mock(return_value=httpx.Response(503))
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(503))
        mock.get(f"{FX_V2}/status/{NOTE_ID}").mock(
            return_value=httpx.Response(200, json=cassette("status_note.json"))
        )
        async with httpx.AsyncClient() as http:
            post = await _reader(http).fetch_target(PostRef(NOTE_ID, "XDevelopers"))
    assert post.text.startswith("To help address automated reply spam")


async def test_read_target_plain_fetcher_uses_fetch() -> None:
    class Plain:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self, ref: PostRef) -> Any:
            self.calls += 1
            return parse_fxtwitter(ref, cassette("status_note.json"))

    plain = Plain()
    post = await read_target(plain, PostRef(NOTE_ID))
    assert plain.calls == 1 and post.id == NOTE_ID


# --- /2/conversation ---


async def test_fetch_replies_top_by_likes_without_media_only() -> None:
    async with respx.mock() as mock:
        route = mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(
            return_value=httpx.Response(200, json=cassette("conversation_p1.json"))
        )
        async with httpx.AsyncClient() as http:
            replies = await _reader(http).fetch_replies(NOTE_ID, limit=30)
    assert route.call_count == 1  # 33 ответа с текстом на первой странице — хватило
    assert route.calls.last.request.url.params.get("ranking_mode") == "likes"
    assert len(replies) == 30
    likes = [r.metrics.likes if r.metrics else 0 for r in replies]
    assert likes == sorted(likes, reverse=True) and likes[0] == 499
    first = replies[0]
    assert first.text == "When can we block or mute entire countries or continents?"
    assert first.author_handle == "FreedomDude71" and first.lang == "en"
    assert first.metrics is not None and first.metrics.impressions == 23120
    assert all(r.text and not r.text.startswith("@XDevelopers") for r in replies)
    assert all(r.in_reply_to_id == NOTE_ID for r in replies)


async def test_fetch_replies_pages_with_cursor_and_pause() -> None:
    p1, p2 = cassette("conversation_p1.json"), cassette("conversation_p2.json")
    sleeps = Sleeps()
    async with respx.mock() as mock:
        route = mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(
            side_effect=[httpx.Response(200, json=p1), httpx.Response(200, json=p2)]
        )
        async with httpx.AsyncClient() as http:
            replies = await _reader(http, sleeps).fetch_replies(NOTE_ID, limit=50)
    assert route.call_count == 2
    assert route.calls[1].request.url.params.get("cursor") == p1["cursor"]["bottom"]
    assert len(replies) == 50 and len({r.id for r in replies}) == 50
    assert sleeps.calls and all(s > 0 for s in sleeps.calls)  # пауза между страницами


async def test_fetch_replies_skips_authors_own_thread_parts() -> None:
    data = cassette("conversation_self_thread.json")
    data.pop("cursor", None)
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/conversation/{NAVAL_ROOT}").mock(
            return_value=httpx.Response(200, json=data)
        )
        async with httpx.AsyncClient() as http:
            replies = await _reader(http).fetch_replies(NAVAL_ROOT, limit=30)
    assert replies and all(r.author_handle != "naval" for r in replies)
    likes = [r.metrics.likes if r.metrics else 0 for r in replies]
    assert likes == sorted(likes, reverse=True)


async def test_fetch_replies_service_down_raises_clear_error() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(return_value=httpx.Response(502))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XUnavailable):
                await _reader(http).fetch_replies(NOTE_ID)


async def test_fetch_replies_later_page_failure_keeps_first_page() -> None:
    p1 = cassette("conversation_p1.json")
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(
            side_effect=[httpx.Response(200, json=p1), httpx.ConnectTimeout("slow")]
        )
        async with httpx.AsyncClient() as http:
            replies = await _reader(http).fetch_replies(NOTE_ID, limit=50)
    assert len(replies) == 33


async def test_fetch_replies_stops_at_max_pages() -> None:
    p1 = cassette("conversation_p1.json")
    async with respx.mock() as mock:
        route = mock.get(f"{FX_V2}/conversation/{NOTE_ID}").mock(
            return_value=httpx.Response(200, json=p1)  # тот же cursor, те же ответы
        )
        async with httpx.AsyncClient() as http:
            replies = await _reader(http).fetch_replies(NOTE_ID, limit=500)
    assert route.call_count <= 5
    assert len(replies) == 33  # повторы не дублируются


# --- /2/status/{id}/quotes ---


async def test_fetch_quotes_pages_and_sorts_by_likes() -> None:
    q1, q2 = cassette("quotes_p1.json"), cassette("quotes_p2.json")
    q2.pop("cursor", None)
    async with respx.mock() as mock:
        route = mock.get(f"{FX_V2}/status/{NOTE_ID}/quotes").mock(
            side_effect=[httpx.Response(200, json=q1), httpx.Response(200, json=q2)]
        )
        async with httpx.AsyncClient() as http:
            quotes = await _reader(http).fetch_quotes(NOTE_ID, limit=30)
    assert route.call_count == 2
    assert route.calls[1].request.url.params.get("cursor") == q1["cursor"]["bottom"]
    assert len(quotes) == 30
    likes = [q.metrics.likes if q.metrics else 0 for q in quotes]
    assert likes == sorted(likes, reverse=True) and likes[0] == 1758
    assert all(q.text for q in quotes)


async def test_fetch_quotes_service_down() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/status/{NOTE_ID}/quotes").mock(side_effect=httpx.ConnectError("x"))
        async with httpx.AsyncClient() as http:
            with pytest.raises(XUnavailable):
                await _reader(http).fetch_quotes(NOTE_ID)


# --- /2/profile ---


async def test_fetch_profile() -> None:
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/profile/NASA").mock(
            return_value=httpx.Response(200, json=cassette("profile_nasa.json"))
        )
        mock.get(f"{FX_V2}/profile/nobody").mock(
            return_value=httpx.Response(404, json={"code": 404, "message": "User not found"})
        )
        async with httpx.AsyncClient() as http:
            reader = _reader(http)
            prof = await reader.fetch_profile("NASA")
            with pytest.raises(XNotFound):
                await reader.fetch_profile("nobody")
    assert prof.handle == "NASA" and prof.id == "11348282"
    assert prof.followers == 92225287 and prof.following == 115 and prof.statuses == 74555
    assert prof.description and prof.name == "NASA"


# --- ограничитель ---


async def test_requests_are_paced() -> None:
    sleeps = Sleeps()
    async with respx.mock() as mock:
        mock.get(f"{FX_V2}/profile/NASA").mock(
            return_value=httpx.Response(200, json=cassette("profile_nasa.json"))
        )
        async with httpx.AsyncClient() as http:
            reader = _reader(http, sleeps)
            for _ in range(3):
                await reader.fetch_profile("NASA")
    assert len(sleeps.calls) == 2 and all(s == pytest.approx(0.1) for s in sleeps.calls)
