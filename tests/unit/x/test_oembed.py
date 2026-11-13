"""Этап 6: чтение поста через oEmbed (publish.x.com)."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef, XPost
from xcopilot.x.oembed import OEMBED_URL, OEmbedReader

REF = PostRef("1855000000000000001", "devexample")


def _payload(p_html: str, *, date: str = "November 10, 2026") -> dict[str, object]:
    html = (
        '<blockquote class="twitter-tweet"><p lang="ru" dir="ltr">'
        f"{p_html}</p>&mdash; Dev Example (@devexample) "
        '<a href="https://twitter.com/devexample/status/1855000000000000001?ref_src=twsrc%5Etfw">'
        f"{date}</a></blockquote>\n"
    )
    return {
        "url": "https://twitter.com/devexample/status/1855000000000000001",
        "author_name": "Dev Example",
        "author_url": "https://twitter.com/devexample",
        "html": html,
        "width": 550,
        "height": None,
        "type": "rich",
        "cache_age": "3153600000",
        "provider_name": "Twitter",
        "provider_url": "https://twitter.com",
        "version": "1.0",
    }


async def _fetch(payload: dict[str, object] | None, status: int = 200) -> XPost:
    async with respx.mock(assert_all_called=True) as mock:
        route = mock.get(OEMBED_URL).mock(return_value=httpx.Response(status, json=payload))
        async with httpx.AsyncClient() as http:
            post = await OEmbedReader(http).fetch(REF)
        params = route.calls.last.request.url.params
        assert params["url"].endswith("/status/1855000000000000001")
        assert params["omit_script"] in ("1", "true")
        return post


async def test_oembed_extracts_text_author_date() -> None:
    post = await _fetch(
        _payload(
            'Первая строка &amp; вторая<br>Третья &lt;3 <a href="https://twitter.com/'
            'hashtag/AI?src=hash&amp;ref_src=twsrc%5Etfw">#AI</a> '
            '<a href="https://twitter.com/someone?ref_src=twsrc%5Etfw">@someone</a>'
        )
    )
    assert post.id == "1855000000000000001"
    assert post.text == "Первая строка & вторая\nТретья <3 #AI @someone"
    assert post.author_handle == "devexample"
    assert post.author_name == "Dev Example"
    assert post.created_at == datetime(2026, 11, 10, tzinfo=UTC)
    assert post.source == "oembed"
    assert post.metrics is None
    assert post.possibly_truncated is False


async def test_oembed_strips_tco_and_media_links() -> None:
    post = await _fetch(
        _payload(
            'Читай тут <a href="https://t.co/AbCdEf1234">example.com/post</a> '
            'и смотри <a href="https://t.co/ZzZz999">https://t.co/ZzZz999</a> '
            '<a href="https://t.co/PicPic1">pic.twitter.com/PicPic1</a> '
            '<a href="https://t.co/PicPic2">pic.x.com/PicPic2</a>'
        )
    )
    assert post.text == "Читай тут example.com/post и смотри"
    assert "t.co" not in post.text
    assert "pic." not in post.text


async def test_oembed_flags_possible_truncation() -> None:
    long_text = "Длинный пост " * 20 + "и тут обрыв…"
    post = await _fetch(
        _payload(f'{long_text} <a href="https://t.co/More1">https://t.co/More1</a>')
    )
    assert post.text == long_text
    assert post.possibly_truncated is True


@pytest.mark.parametrize("status", [404, 403])
async def test_oembed_not_found_returns_typed_error(status: int) -> None:
    with pytest.raises(XNotFound) as exc:
        await _fetch({"error": "not found"}, status=status)
    assert REF.id in str(exc.value)


async def test_oembed_server_error_is_unavailable() -> None:
    with pytest.raises(XUnavailable):
        await _fetch(None, status=503)
