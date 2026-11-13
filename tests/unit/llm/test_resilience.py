"""Повторы на 429/5xx/таймаутах и предохранитель провайдера."""

import pytest

from tests.fakes import FakeClock
from xcopilot.llm.errors import (
    CircuitOpen,
    PermanentProviderError,
    ProviderUnavailable,
    TransientProviderError,
)
from xcopilot.llm.resilience import CircuitBreaker, RetryPolicy, call_with_retry


def state(breaker: CircuitBreaker) -> str:
    return breaker.state


class Flaky:
    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_retry_on_429_then_success() -> None:
    fn = Flaky(TransientProviderError("anthropic", status=429))
    sleep = Sleeps()
    assert await call_with_retry(fn, RetryPolicy(), sleep=sleep) == "ok"
    assert fn.calls == 2
    assert len(sleep.delays) == 1
    assert 0 < sleep.delays[0] <= RetryPolicy().max_delay


async def test_retry_gives_up_after_three_retries() -> None:
    fn = Flaky(*[TransientProviderError("deepseek", status=503) for _ in range(10)])
    sleep = Sleeps()
    with pytest.raises(TransientProviderError):
        await call_with_retry(fn, RetryPolicy(retries=3), sleep=sleep)
    assert fn.calls == 4  # первый вызов + 3 повтора
    assert len(sleep.delays) == 3
    # пауза с разбросом: растёт, но не выше потолка
    assert all(0 < d <= RetryPolicy().max_delay for d in sleep.delays)


async def test_no_retry_on_400() -> None:
    fn = Flaky(PermanentProviderError("anthropic", status=400))
    sleep = Sleeps()
    with pytest.raises(PermanentProviderError):
        await call_with_retry(fn, RetryPolicy(), sleep=sleep)
    assert fn.calls == 1
    assert sleep.delays == []


def test_circuit_opens_after_failures_and_half_opens_after_pause() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker("jev", clock, failure_threshold=3, reset_after=30.0)
    for _ in range(2):
        breaker.before_call()
        breaker.on_failure()
    assert state(breaker) == "closed"
    breaker.before_call()
    breaker.on_success()  # успех обнуляет счётчик «подряд»
    for _ in range(3):
        breaker.before_call()
        breaker.on_failure()
    assert state(breaker) == "open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    assert issubclass(CircuitOpen, ProviderUnavailable)

    clock.advance(29)
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    clock.advance(1)
    breaker.before_call()  # пробный вызов разрешён
    assert state(breaker) == "half_open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()  # второй параллельный пробный — нет
    breaker.on_failure()
    assert state(breaker) == "open"  # проба не прошла — снова пауза

    clock.advance(30)
    breaker.before_call()
    breaker.on_success()
    assert state(breaker) == "closed"
    breaker.before_call()
