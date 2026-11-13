"""Расчёт стоимости вызовов: Claude с кэшем, DeepSeek с пиковыми часами, Jev только вход."""

from datetime import UTC, datetime

import pytest

from xcopilot.llm.pricing import PricingTable, Usage, is_deepseek_peak, price

TUESDAY = datetime(2026, 11, 10, tzinfo=UTC)  # вторник
SATURDAY = datetime(2026, 11, 14, tzinfo=UTC)  # суббота


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


def test_cost_claude_with_cache_read_and_write() -> None:
    usage = Usage(
        input_tokens=10_000, output_tokens=2_000, cache_read_tokens=50_000, cache_write_tokens=8_000
    )
    # $4 × 0.01 + $20 × 0.002 + $0.20 × 0.05 + $5 × 0.008 = $0.13
    assert price("anthropic", "claude-opus-5-5", usage, TUESDAY) == 130_000
    # дробная часть микродоллара округляется вверх (бюджет не занижается)
    assert price("anthropic", "claude-opus-5-5", Usage(cache_read_tokens=1), TUESDAY) == 1
    assert price("anthropic", "claude-opus-5-5", Usage(input_tokens=333), TUESDAY) == 1_332
    assert price("anthropic", "claude-opus-5-5", Usage(), TUESDAY) == 0


def test_cost_deepseek_peak_vs_offpeak() -> None:
    assert is_deepseek_peak(at(TUESDAY, 2)) is True
    assert is_deepseek_peak(at(SATURDAY, 2)) is False
    # границы: [01, 04) и [06, 10) UTC по будням
    assert is_deepseek_peak(at(TUESDAY, 1)) is True
    assert is_deepseek_peak(at(TUESDAY, 0, 59)) is False
    assert is_deepseek_peak(at(TUESDAY, 3, 59)) is True
    assert is_deepseek_peak(at(TUESDAY, 4)) is False
    assert is_deepseek_peak(at(TUESDAY, 5, 59)) is False
    assert is_deepseek_peak(at(TUESDAY, 6)) is True
    assert is_deepseek_peak(at(TUESDAY, 9, 59)) is True
    assert is_deepseek_peak(at(TUESDAY, 10)) is False
    # пояс момента не важен: сравнивается время UTC
    from zoneinfo import ZoneInfo

    minsk = datetime(2026, 11, 10, 5, 0, tzinfo=ZoneInfo("Europe/Minsk"))  # 02:00 UTC
    assert is_deepseek_peak(minsk) is True

    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert price("deepseek", "deepseek-flash", usage, at(SATURDAY, 2)) == 750_000
    assert price("deepseek", "deepseek-flash", usage, at(TUESDAY, 2)) == 1_500_000
    assert price("deepseek", "deepseek-flash", usage, at(TUESDAY, 4)) == 750_000


def test_cost_jev_counts_input_only() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert price("jev", "jev-latest", usage, TUESDAY) == 42_000
    assert price("jev", "jev-latest", Usage(output_tokens=10_000_000), TUESDAY) == 0


def test_pricing_table_has_verification_date_and_provider_default() -> None:
    table = PricingTable.load()
    assert table.verified.isoformat() == "2026-11-12"
    # модель без своей строки берёт цену по умолчанию своего провайдера
    assert table.lookup("anthropic", "claude-unknown-9") == table.lookup("anthropic", "default")
    with pytest.raises(KeyError):
        table.lookup("nope", "x")
