"""Публичные цифры поста из fxtwitter (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

import httpx
import respx

from xcopilot.x.models import PostRef
from xcopilot.x.oembed import OEMBED_URL
from xcopilot.x.public_read import FXTWITTER_URL, PublicPostReader, parse_fxtwitter

REF = PostRef("1855000000000000001", "devexample")
FX = f"{FXTWITTER_URL}/2/status/1855000000000000001"  # FxEmbed v2: без хэндла


def _fx(*, views: int | None = 12000, followers: int | None = 5400) -> dict[str, object]:
    """Форма ответа как у настоящего api.fxtwitter.com (проверено 18.11.2026)."""
    author: dict[str, object] = {"screen_name": "devexample", "name": "Dev", "id": "42"}
    if followers is not None:
        author["followers"] = followers
    return {
        "code": 200,
        "message": "OK",
        "tweet": {
            "id": "1855000000000000001",
            "text": "Полный текст поста",
            "author": author,
            "likes": 340,
            "retweets": 25,
            "replies": 18,
            "quotes": 4,
            "bookmarks": 31,
            "views": views,
            "created_timestamp": 1762783402,
        },
    }


def test_fxtwitter_gives_views_bookmarks_and_followers() -> None:
    post = parse_fxtwitter(REF, _fx())
    assert post.metrics is not None
    m = post.metrics
    assert (m.likes, m.reposts, m.replies, m.quotes, m.bookmarks) == (340, 25, 18, 4, 31)
    assert m.impressions == 12000
    assert post.author_followers == 5400


def test_fxtwitter_without_views_and_followers() -> None:
    post = parse_fxtwitter(REF, _fx(views=None, followers=None))
    assert post.metrics is not None
    assert post.metrics.impressions is None
    assert post.author_followers is None


async def test_oembed_text_is_enriched_with_fxtwitter_numbers() -> None:
    oembed = {
        "author_name": "Dev",
        "author_url": "https://twitter.com/devexample",
        "html": '<blockquote class="twitter-tweet"><p lang="ru">Привет мир</p></blockquote>',
    }
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=oembed))
        mock.get(FX).mock(return_value=httpx.Response(200, json=_fx()))
        async with httpx.AsyncClient() as http:
            post = await PublicPostReader(http).get_post(REF)
    assert post.text == "Привет мир" and post.source == "oembed"
    assert post.metrics is not None and post.metrics.likes == 340
    assert post.author_followers == 5400


async def test_oembed_kept_without_numbers_when_fxtwitter_down() -> None:
    oembed = {
        "author_name": "Dev",
        "author_url": "https://twitter.com/devexample",
        "html": '<blockquote class="twitter-tweet"><p lang="ru">Привет мир</p></blockquote>',
    }
    async with respx.mock() as mock:
        mock.get(OEMBED_URL).mock(return_value=httpx.Response(200, json=oembed))
        mock.get(FX).mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as http:
            post = await PublicPostReader(http).get_post(REF)
    assert post.text == "Привет мир"
    assert post.metrics is None
