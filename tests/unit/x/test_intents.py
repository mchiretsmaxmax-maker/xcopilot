"""Этап 6: ссылки Web Intent и подсчёт длины по правилам X."""

from urllib.parse import parse_qs, urlsplit

from xcopilot.x.intents import (
    build_post_intent,
    build_quote_intent,
    build_reply_intent,
    weighted_len,
)


def _query(url: str) -> dict[str, list[str]]:
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://x.com/intent/tweet"
    return parse_qs(parts.query, keep_blank_values=True)


def test_intent_post_url_encoding() -> None:
    text = "Привет 🚀\nвторая строка & #тег ?a=b+c 100%"
    url = build_post_intent(text)
    assert url is not None
    raw_query = urlsplit(url).query
    for ch in ("\n", " ", "&тег", "#", "+", "🚀"):
        assert ch not in raw_query
    assert "%0A" in raw_query
    assert "%23" in raw_query
    assert "%26" in raw_query
    assert _query(url) == {"text": [text]}


def test_intent_reply_has_in_reply_to() -> None:
    url = build_reply_intent("согласен", "1855000000000000001")
    assert url is not None
    assert _query(url) == {"text": ["согласен"], "in_reply_to": ["1855000000000000001"]}


def test_intent_quote_appends_url() -> None:
    quoted = "https://x.com/devexample/status/1855000000000000001"
    url = build_quote_intent("Вот почему это важно", quoted)
    assert url is not None
    assert _query(url) == {"text": [f"Вот почему это важно {quoted}"]}


def test_weighted_len_counts_url_as_23_and_wide_chars_as_2() -> None:
    assert weighted_len("") == 0
    assert weighted_len("hello") == 5
    assert weighted_len("привет") == 6  # кириллица — узкие символы
    assert weighted_len("日本語") == 6
    assert weighted_len("🚀") == 2
    assert weighted_len("👍🏽") == 2  # эмодзи с оттенком кожи — одна единица
    assert weighted_len("👨‍👩‍👧") == 2  # ZWJ-последовательность — одна единица
    assert weighted_len("❤️") == 2
    assert weighted_len("https://example.com/a/very/long/path?with=query&and=more") == 23
    assert weighted_len("см. http://a.co") == 4 + 23
    assert weighted_len("a https://x.com/b/status/1 b https://t.co/x") == 1 + 1 + 23 + 3 + 23
    assert weighted_len("é") == 1  # NFC: é — один символ
    assert weighted_len("a\nb") == 3


def test_intent_none_when_over_280() -> None:
    assert build_post_intent("a" * 280) is not None
    assert build_post_intent("a" * 281) is None
    assert build_post_intent("日" * 140) is not None
    assert build_post_intent("日" * 141) is None
    assert build_reply_intent("a" * 281, "1") is None
    # 256 + пробел + ссылка 23 = 280 — помещается; на знак больше — уже нет
    quoted = "https://x.com/a/status/1"
    assert build_quote_intent("a" * 256, quoted) is not None
    assert build_quote_intent("a" * 257, quoted) is None
