from datetime import UTC, datetime

from tests.fakes.clock import FakeClock
from xcopilot.clock import Clock, SystemClock


def test_fake_clock_advances() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FakeClock(start)
    assert clock.now() == start
    clock.advance(60)
    assert clock.now() == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)


def test_system_clock_returns_aware_utc() -> None:
    clock: Clock = SystemClock()
    assert clock.now().tzinfo is not None
