"""Этап 6: разбор ссылок на посты и хэндлов."""

import pytest

from xcopilot.x.models import PostRef
from xcopilot.x.urls import extract_post_urls, parse_handle, parse_post_url

ID = "1855000000000000001"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"https://x.com/devexample/status/{ID}", PostRef(ID, "devexample")),
        (f"https://twitter.com/devexample/status/{ID}", PostRef(ID, "devexample")),
        (f"http://www.twitter.com/devexample/status/{ID}", PostRef(ID, "devexample")),
        (f"https://mobile.twitter.com/devexample/status/{ID}", PostRef(ID, "devexample")),
        (f"https://mobile.x.com/Dev_Example/status/{ID}", PostRef(ID, "Dev_Example")),
        (f"https://x.com/i/web/status/{ID}", PostRef(ID, None)),
        (f"https://twitter.com/i/status/{ID}", PostRef(ID, None)),
        (f"https://x.com/devexample/status/{ID}?s=20", PostRef(ID, "devexample")),
        (f"https://x.com/devexample/status/{ID}?s=20&t=AbC-dEf_123", PostRef(ID, "devexample")),
        (f"https://x.com/devexample/status/{ID}/photo/1", PostRef(ID, "devexample")),
        (f"https://twitter.com/devexample/statuses/{ID}", PostRef(ID, "devexample")),
        (f"x.com/devexample/status/{ID}", PostRef(ID, "devexample")),
        (f"смотри тут: https://x.com/devexample/status/{ID}!", PostRef(ID, "devexample")),
        ("https://x.com/devexample", None),
        ("https://example.com/devexample/status/123", None),
        ("https://x.com/devexample/status/abc", None),
        ("просто текст", None),
        ("", None),
    ],
)
def test_parse_post_url_variants(text: str, expected: PostRef | None) -> None:
    assert parse_post_url(text) == expected


def test_post_ref_url_is_canonical() -> None:
    assert PostRef(ID, "devexample").url == f"https://x.com/devexample/status/{ID}"
    assert PostRef(ID).url == f"https://x.com/i/web/status/{ID}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@devexample", "devexample"),
        ("devexample", "devexample"),
        ("  @Dev_Example  ", "Dev_Example"),
        ("https://x.com/devexample", "devexample"),
        ("https://twitter.com/devexample/", "devexample"),
        ("https://mobile.x.com/devexample?s=21", "devexample"),
        ("x.com/devexample", "devexample"),
        (f"https://x.com/devexample/status/{ID}", "devexample"),
        ("@a", "a"),
        ("@abcdefghijklmno", "abcdefghijklmno"),
        ("@abcdefghijklmnop", None),
        ("@dev-example", None),
        ("@", None),
        ("", None),
        ("dev example", None),
        ("https://x.com/home", None),
        ("https://x.com/i/web/status/1", None),
        ("https://example.com/devexample", None),
    ],
)
def test_parse_handle_variants(text: str, expected: str | None) -> None:
    assert parse_handle(text) == expected


def test_extract_multiple_urls_from_text() -> None:
    text = (
        "Два поста: https://x.com/a/status/111 и https://twitter.com/b/status/222?s=20,\n"
        "повтор https://mobile.x.com/a/status/111/photo/1 и ещё x.com/i/web/status/333."
        " Не пост: https://x.com/c"
    )
    assert extract_post_urls(text) == [PostRef("111", "a"), PostRef("222", "b"), PostRef("333")]
    assert extract_post_urls("ничего") == []
