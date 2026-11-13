"""Разбор ввода: хэндлы, примеры стиля, деньги, часовые пояса."""

import pytest

from xcopilot.bot.parsing import (
    classify_sample,
    normalize_handle,
    parse_handles,
    parse_timezone,
    parse_usd_to_micro,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@Name", "name"),
        ("x.com/name", "name"),
        ("https://twitter.com/name/", "name"),
        ("https://x.com/Name?s=20", "name"),
        ("name", "name"),
        ("  @user_1  ", "user_1"),
        ("", None),
        ("hello world", None),
        ("https://example.com/name", None),
        ("x.com/name/status/123", None),
        ("a" * 16, None),
        ("@@x", None),
        ("!!!", None),
    ],
)
def test_normalize_handle(raw: str, expected: str | None) -> None:
    assert normalize_handle(raw) == expected


def test_parse_handles_splits_and_dedups() -> None:
    valid, garbage = parse_handles("@Name, x.com/other\nhttps://twitter.com/name/ !!!")
    assert valid == ["name", "other"]
    assert garbage == ["!!!"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Мой пост про BTC и ончейн", "text"),
        ("gm", "text"),
        ("https://x.com/user/status/123", "url"),
        ("https://twitter.com/user/status/9?s=1", "url"),
        ("@alice", "handle"),
        ("https://x.com/alice", "handle"),
    ],
)
def test_classify_sample(text: str, kind: str) -> None:
    assert classify_sample(text)[0] == kind


def test_classify_sample_handle_value_is_normalized() -> None:
    assert classify_sample("@Alice") == ("handle", "alice")


@pytest.mark.parametrize(
    ("text", "micro"),
    [("5", 5_000_000), ("$2.5", 2_500_000), ("7,5", 7_500_000), ("0.000001", 1)],
)
def test_parse_usd_ok(text: str, micro: int) -> None:
    assert parse_usd_to_micro(text) == micro


@pytest.mark.parametrize("text", ["0", "-1", "abc", "", "1001", "1e3"])
def test_parse_usd_rejects(text: str) -> None:
    assert parse_usd_to_micro(text) is None


def test_parse_timezone() -> None:
    assert parse_timezone("Europe/Berlin") == "Europe/Berlin"
    assert parse_timezone(" Europe/Berlin ") == "Europe/Berlin"
    assert parse_timezone("Mars/Base") is None
    assert parse_timezone("") is None
