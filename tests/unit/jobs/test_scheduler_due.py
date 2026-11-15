"""Когда расписание «наступило» (чистая функция due_at)."""

from __future__ import annotations

from datetime import UTC, datetime, time

from xcopilot.jobs.scheduler import due_at

T3 = time(3, 0)


def test_daily_due_after_local_time() -> None:
    now = datetime(2026, 3, 10, 1, 0, tzinfo=UTC)  # 04:00 в Минске (UTC+3)
    assert due_at(now, "Europe/Minsk", T3, None, None) == datetime(2026, 3, 10, 0, 0, tzinfo=UTC)


def test_not_due_before_local_time() -> None:
    now = datetime(2026, 3, 9, 23, 30, tzinfo=UTC)  # 02:30 в Минске
    assert due_at(now, "Europe/Minsk", T3, None, None) is None


def test_not_due_when_already_run() -> None:
    now = datetime(2026, 3, 10, 5, 0, tzinfo=UTC)
    ran = datetime(2026, 3, 10, 3, 1, tzinfo=UTC)
    assert due_at(now, "UTC", T3, None, ran) is None
    yesterday = datetime(2026, 3, 9, 3, 1, tzinfo=UTC)
    assert due_at(now, "UTC", T3, None, yesterday) == datetime(2026, 3, 10, 3, 0, tzinfo=UTC)


def test_weekday_must_match() -> None:
    tuesday = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # 10.03.2026 — вторник
    assert due_at(tuesday, "UTC", T3, 1, None) is not None
    assert due_at(tuesday, "UTC", T3, 0, None) is None
