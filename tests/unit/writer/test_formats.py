"""Форматы черновиков: длины поста, части треда, ответ и цитата требуют целевой пост."""

from __future__ import annotations

from xcopilot.writer.formats import FORMATS, FormatCheck, validate_format


def test_format_validator_flags_long_post() -> None:
    ok = FormatCheck(format="post", text="а" * 280)
    long = FormatCheck(format="post", text="а" * 281)
    assert validate_format(ok) == []
    assert "too_long" in validate_format(long)
    # паспорт разрешил длинные посты — 281 знак уже не ошибка, но и у длинного есть потолок
    assert validate_format(FormatCheck(format="post", text="а" * 281, long_max_len=2000)) == []
    assert "too_long" in validate_format(
        FormatCheck(format="post", text="а" * 2001, long_max_len=2000)
    )
    assert FORMATS["post"].max_len == 280


def test_thread_parts_count_and_length() -> None:
    good = ["часть"] * 3
    assert validate_format(FormatCheck(format="thread", text="", parts=good)) == []
    assert validate_format(FormatCheck(format="thread", text="", parts=["часть"] * 8)) == []
    assert "thread_too_few_parts" in validate_format(
        FormatCheck(format="thread", text="", parts=["часть"] * 2)
    )
    assert "thread_too_many_parts" in validate_format(
        FormatCheck(format="thread", text="", parts=["часть"] * 9)
    )
    issues = validate_format(FormatCheck(format="thread", text="", parts=["ок", "б" * 281, "ок"]))
    assert "part_too_long:2" in issues
    assert (FORMATS["thread"].min_parts, FORMATS["thread"].max_parts) == (3, 8)


def test_reply_requires_target_post() -> None:
    assert "needs_target" in validate_format(FormatCheck(format="reply", text="согласен"))
    assert "needs_target" in validate_format(FormatCheck(format="quote", text="вот так"))
    with_url = FormatCheck(format="reply", text="согласен", reply_to_url="https://x.com/a/status/1")
    assert validate_format(with_url) == []
    assert validate_format(FormatCheck(format="reply", text="да", has_target=True)) == []
    assert "too_long" in validate_format(
        FormatCheck(format="reply", text="а" * 281, has_target=True)
    )
