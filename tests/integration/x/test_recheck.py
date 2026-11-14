"""Этап 6 (v3.1): обработчик x.recheck_metrics дописывает сигнал к материалу."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tests.fakes import FakeClock
from tests.fakes.x import FakeX
from xcopilot.db.repos import MaterialSignalsRepo, SourcesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job
from xcopilot.x.models import XMetrics, XPost
from xcopilot.x.recheck import handle_recheck_metrics

T = datetime(2026, 11, 14, 10, 30, tzinfo=UTC)


def _job(account_id: UUID, post_id: str) -> Job:
    return Job(
        id=UUID(int=1),
        account_id=account_id,
        kind="x.recheck_metrics",
        payload={"post_id": post_id, "handle": "devexample"},
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=T,
        locked_by="w",
        last_error=None,
    )


_METRICS = XMetrics(likes=7, impressions=90)


def _post(source: str = "api", metrics: XMetrics | None = _METRICS) -> XPost:
    return XPost(
        id="777",
        text="t",
        author_handle="devexample",
        created_at=datetime(2026, 11, 13, 10, 0, tzinfo=UTC),
        source=source,  # type: ignore[arg-type]
        metrics=metrics,
    )


async def _material(uow_factory: Callable[[], UnitOfWork], account_id: UUID) -> None:
    async with uow_factory() as uow:
        await SourcesRepo(uow.session, account_id).add_or_get(
            kind="post",
            stance="inspiration",
            origin="x_api",
            content_hash="h-777",
            external_id="777",
        )


async def _signals(uow_factory: Callable[[], UnitOfWork], account_id: UUID) -> int:
    async with uow_factory() as uow:
        return len(await MaterialSignalsRepo(uow.session, account_id).list())


async def test_recheck_appends_signal(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    await _material(uow_factory, account_id)
    fake = FakeX(mode="lookup")
    fake.add_post(_post())
    ok = await handle_recheck_metrics(
        _job(account_id, "777"),
        client_for=lambda _: fake,
        uow_factory=uow_factory,
        clock=xclock,
    )
    assert ok
    assert await _signals(uow_factory, account_id) == 1


@pytest.mark.parametrize("kind", ["oembed", "no_metrics", "no_material"])
async def test_recheck_skips(
    kind: str, uow_factory: Callable[[], UnitOfWork], account_id: UUID, xclock: FakeClock
) -> None:
    fake = FakeX(mode="lookup")
    if kind == "oembed":
        fake.add_post(_post("oembed"))
    elif kind == "no_metrics":
        fake.add_post(_post(metrics=None))
    else:
        fake.add_post(_post())
    if kind != "no_material":
        await _material(uow_factory, account_id)
    ok = await handle_recheck_metrics(
        _job(account_id, "777"),
        client_for=lambda _: fake,
        uow_factory=uow_factory,
        clock=xclock,
    )
    assert not ok
    assert await _signals(uow_factory, account_id) == 0
