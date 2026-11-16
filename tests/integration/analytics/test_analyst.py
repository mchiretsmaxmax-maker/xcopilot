"""Этап 14, задача 7: отчёт аналитика — модель пересказывает посчитанное."""

from __future__ import annotations

from tests.analytics_stand import AStand
from tests.integration.analytics.test_patterns import _day, seed_fixture
from xcopilot.analytics.analyst import Analyst, AnalystOut, Claim


def _analyst(st: AStand) -> Analyst:
    return Analyst(uow_factory=st.b.uow, gateway=st.report_llm, clock=st.clock)  # type: ignore[arg-type]


async def _morning_ids(st: AStand) -> list[str]:
    rows = await st.rows(
        "SELECT external_id FROM sources WHERE account_id = :a"
        " AND (posted_at AT TIME ZONE 'UTC')::time = '09:00'"
        " ORDER BY external_id",
        a=st.account_id,
    )
    return [r[0] for r in rows]


async def test_report_claims_reference_post_ids(st: AStand) -> None:
    await seed_fixture(st)
    morning = await _morning_ids(st)
    st.report_llm.push(
        AnalystOut(
            worked=[
                Claim(text="Утренние посты собирают больше показов", post_ids=morning[:3]),
                Claim(text="Всё и так хорошо", post_ids=[]),  # без доказательств — отброшено
            ],
            improve=[
                Claim(text="Пиши чаще утром", post_ids=["no-such-post"]),  # чужой id — отброшено
                Claim(text="Отвечай реже: ответы слабее", post_ids=morning[:1]),
            ],
        )
    )
    report = await _analyst(st).build_report(st.account_id)
    assert [c.text for c in report.worked] == ["Утренние посты собирают больше показов"]
    assert [c.text for c in report.improve] == ["Отвечай реже: ответы слабее"]
    assert report.dropped == 2
    assert report.warning is None


async def test_report_numbers_match_computed_patterns(st: AStand) -> None:
    await seed_fixture(st)
    morning = await _morning_ids(st)
    st.report_llm.push(
        AnalystOut(
            worked=[
                # +200%, 7 постов и час 9 есть в расчёте
                Claim(text="Посты в 9 часов: +200% к обычному на 7 постах", post_ids=morning[:2]),
                # «в 12 раз» посчитано не было
                Claim(text="Утром показов в 12 раз больше", post_ids=morning[:2]),
            ],
            improve=[],
        )
    )
    report = await _analyst(st).build_report(st.account_id)
    assert [c.text for c in report.worked] == ["Посты в 9 часов: +200% к обычному на 7 постах"]
    assert report.dropped == 1
    # модель видит посчитанные факты, а не сырые данные
    assert "+200%" in str(st.report_llm.calls[0]["variables"])


async def test_small_sample_warning_in_report(st: AStand) -> None:
    for i in range(6):
        await st.seed_post(f"Пост {i}", posted_at=_day(st, i + 1, 9), views=500 + i)
    st.report_llm.push(AnalystOut(worked=[], improve=[]))
    report = await _analyst(st).build_report(st.account_id)
    assert report.warning is not None and "6" in report.warning and "10" in report.warning


async def test_report_without_model_shows_numbers_only(st: AStand) -> None:
    from xcopilot.llm.errors import ProviderUnavailable

    await seed_fixture(st)
    st.report_llm.push(ProviderUnavailable("down"))
    report = await _analyst(st).build_report(st.account_id)
    assert report.worked == [] and report.improve == []
    assert report.warning is not None and "Модель недоступна" in report.warning
    assert report.patterns.items  # цифры остаются
