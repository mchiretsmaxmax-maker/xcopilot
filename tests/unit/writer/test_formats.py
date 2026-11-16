"""Форматы черновиков: длины поста, части треда, ответ и цитата требуют целевой пост."""

from __future__ import annotations

from xcopilot.writer.formats import FormatCheck, validate_format


def test_post_has_no_280_ceiling() -> None:
    # смена требования владельца 16.11.2026: длинный пост режет код, а не формат-проверка
    assert validate_format(FormatCheck(format="post", text="а" * 281)) == []
    assert validate_format(FormatCheck(format="post", text="а" * 5000)) == []
    assert validate_format(FormatCheck(format="post", text="  ")) == ["empty"]


def test_thread_needs_at_least_one_part_each_within_limit() -> None:
    assert validate_format(FormatCheck(format="thread", text="", parts=["часть"])) == []
    assert validate_format(FormatCheck(format="thread", text="", parts=["часть"] * 12)) == []
    issues = validate_format(FormatCheck(format="thread", text="", parts=["ок", "б" * 281, "ок"]))
    assert "part_too_long:2" in issues
    assert "thread_too_few_parts" in validate_format(
        FormatCheck(format="thread", text="", parts=[])
    )


def test_reply_requires_target_post() -> None:
    assert "needs_target" in validate_format(FormatCheck(format="reply", text="согласен"))
    assert "needs_target" in validate_format(FormatCheck(format="quote", text="вот так"))
    with_url = FormatCheck(format="reply", text="согласен", reply_to_url="https://x.com/a/status/1")
    assert validate_format(with_url) == []
    assert validate_format(FormatCheck(format="reply", text="да", has_target=True)) == []
    assert "too_long" in validate_format(
        FormatCheck(format="reply", text="а" * 281, has_target=True)
    )
