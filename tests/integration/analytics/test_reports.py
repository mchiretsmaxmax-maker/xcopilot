"""Этап 14, задачи 8 и 16: ежедневный отчёт, расписание по местному времени, алерт дрейфа."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from tests.analytics_stand import AStand
from tests.brain_core_stand import knowledge
from tests.integration.db.brain_helpers import evidence
from xcopilot.analytics.drift import drift_alert
from xcopilot.analytics.jobs import handle_reports_daily
from xcopilot.analytics.reports import Reports
from xcopilot.jobs.queue import Job
from xcopilot.jobs.scheduler import Scheduler
from xcopilot.llm.errors import ProviderUnavailable
from xcopilot.metrics.catalog import sync_catalog

DAY = date(2026, 11, 13)  # «вчера» относительно часов стенда (14.11 12:00 UTC)


class DailyText(BaseModel):
    text: str


def _reports(st: AStand) -> Reports:
    return Reports(uow_factory=st.b.uow, gateway=st.report_llm, clock=st.clock)  # type: ignore[arg-type]


async def _seed_day(st: AStand) -> list[str]:
    """3 поста вчера (лучший 41 000), 6 раньше: медиана 25 625 → лучший на +60%."""
    ids = []
    for i, views in enumerate([41_000, 10_000, 5_000]):
        _, xid = await st.seed_post(
            f"Вчерашний пост {i}",
            posted_at=datetime(2026, 11, 13, 8 + i, tzinfo=UTC),
            views=views,
        )
        ids.append(xid)
    for i, views in enumerate([20_000, 25_000, 25_625, 25_625, 26_000, 30_000]):
        await st.seed_post(
            f"Старый пост {i}", posted_at=st.clock.now() - timedelta(days=3 + i), views=views
        )
    async with st.sf() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (account_id, ts, role, provider, model, purpose,"
                " cost_micro_usd, status) VALUES (:a, :t, 'writer', 'anthropic', 'm', 'p', 840000,"
                " 'ok')"
            ),
            {"a": st.account_id, "t": datetime(2026, 11, 13, 10, tzinfo=UTC)},
        )
        await s.commit()
    clean = await st.publish("Чистое одобрение", "9001", datetime(2026, 11, 13, 7, tzinfo=UTC))
    edited = await st.publish("Правка владельца", "9002", datetime(2026, 11, 13, 7, tzinfo=UTC))
    async with st.sf() as s:
        for draft, action, dist in ((clean, "approve", 0), (edited, "edit", 0.4)):
            await s.execute(
                text(
                    "INSERT INTO reviews (account_id, draft_id, user_id, action, text_before,"
                    " edit_distance, created_at) VALUES (:a, :d, :u, :act, 't', :dist, :t)"
                ),
                {
                    "a": st.account_id, "d": draft, "u": st.b.user_id, "act": action,
                    "dist": dist, "t": datetime(2026, 11, 13, 9, tzinfo=UTC),
                },
            )  # fmt: skip
        await s.commit()
    return ids


async def test_daily_report_uses_template_when_cheap_down(st: AStand) -> None:
    await _seed_day(st)
    st.report_llm.push(ProviderUnavailable("deepseek недоступен"))
    out = await _reports(st).daily_report(st.account_id, DAY)
    assert out == (
        "Вчера: 3 поста, лучший — 41 тыс. показов (+60% к обычному)."
        " Без правок одобрено 1 из 2. Расход $0.84."
    )


async def test_daily_report_model_retells_when_numbers_grounded(st: AStand) -> None:
    await _seed_day(st)
    st.report_llm.push(DailyText(text="Вчера вышло 3 поста, лучший набрал 41 тыс. показов."))
    out = await _reports(st).daily_report(st.account_id, DAY)
    assert out == "Вчера вышло 3 поста, лучший набрал 41 тыс. показов."
    st.report_llm.push(DailyText(text="Вчера вышло 30 постов!"))  # 30 не посчитано → шаблон
    out = await _reports(st).daily_report(st.account_id, DAY)
    assert out.startswith("Вчера: 3 поста")


async def test_daily_report_mentions_confirmed_and_refuted_knowledge(st: AStand) -> None:
    await _seed_day(st)
    async with st.sf() as s:
        yes1 = await knowledge(s, st.account_id, statement="Личная цифра в первой строке")
        yes2 = await knowledge(s, st.account_id, statement="Короткие строки читаются лучше")
        no = await knowledge(s, st.account_id, statement="Вопрос в конце поста")
        sid = (
            (
                await s.execute(
                    text(
                        "SELECT id FROM sources WHERE external_id IS NOT NULL"
                        " ORDER BY posted_at DESC"
                    )
                )
            )
            .scalars()
            .first()
        )
        for item, pol in ((yes1, 1), (yes2, 1), (no, -1)):
            await evidence(
                s, st.account_id, item, observed_at=datetime(2026, 11, 13, 9, tzinfo=UTC),
                source_id=sid, basis="own_outcome", polarity=pol, w_scope=2.0,
                created_at=datetime(2026, 11, 13, 12, tzinfo=UTC),
            )  # fmt: skip
        await s.commit()
    st.report_llm.push(ProviderUnavailable("down"))
    out = await _reports(st).daily_report(st.account_id, DAY)
    assert "подтвердил 2 знания и опроверг 1" in out
    assert "Вопрос в конце поста" in out


async def test_daily_report_sent_at_account_local_time(st: AStand) -> None:
    await st.set_account(tz="Asia/Tokyo")  # UTC+9
    st.clock.advance(10.5 * 3600)  # 22:30 UTC = 07:30 по Токио
    sched = Scheduler(st.b.uow, st.clock)
    await sched.tick()
    assert await st.jobs("reports.daily") == []
    st.clock.advance(40 * 60)  # 23:10 UTC = 08:10 по Токио
    await sched.tick()
    assert len(await st.jobs("reports.daily")) == 1


async def test_daily_job_sends_report_and_drift_alert(st: AStand) -> None:
    await _seed_day(st)
    await _drift_rows(st, 0.10, 0.14)
    st.report_llm.push(ProviderUnavailable("down"))
    job = Job(
        id=st.account_id, account_id=st.account_id, kind="reports.daily", payload={},
        status="running", attempts=1, max_attempts=5, run_at=st.clock.now(), locked_by="t",
        last_error=None,
    )  # fmt: skip
    await handle_reports_daily(
        job, reports=_reports(st), notifier=st.notifier, uow_factory=st.b.uow
    )
    texts = [t for _, t, _ in st.notifier.sent]
    assert texts[0].startswith("Вчера: 3 поста")
    assert any("голос" in t.lower() for t in texts[1:])


async def _drift_rows(st: AStand, first: float, last: float) -> None:
    async with st.sf() as s:
        await sync_catalog(s)
        for week, value in ((date(2026, 10, 19), first), (date(2026, 11, 9), last)):
            await s.execute(
                text(
                    "INSERT INTO metric_values (account_id, metric_key, grain, period_start, dims,"
                    " dims_hash, value, sample_size, definition_version, computed_at)"
                    " VALUES (:a, 'brain_voice_drift', 'week', :p, '{}'::jsonb, 'h', :v, 5, 1, :t)"
                ),
                {"a": st.account_id, "p": week, "v": value, "t": st.clock.now()},
            )
        await s.commit()


async def test_drift_alert_above_threshold(st: AStand) -> None:
    await _drift_rows(st, 0.10, 0.14)  # +40% к первой неделе
    async with st.sf() as s:
        alert: Any = await drift_alert(s, st.account_id)
    assert alert is not None and "40%" in alert
    async with st.sf() as s:
        await s.execute(text("DELETE FROM metric_values"))
        await s.commit()
    await _drift_rows(st, 0.10, 0.11)  # +10% — в норме
    async with st.sf() as s:
        assert await drift_alert(s, st.account_id) is None
