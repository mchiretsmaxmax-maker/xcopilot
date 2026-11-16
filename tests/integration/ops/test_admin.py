"""Этап 15 (runbook, задача 14): команды администратора — блокировка автора и переразбор."""

from __future__ import annotations

import pytest

from tests.integration.db.brain_helpers import author, evidence, item, source
from tests.integration.ops.conftest import OpsStand
from xcopilot.ops.admin import AdminError, block_author, reanalyze


async def test_block_author_revokes_evidence_and_blocks(ops: OpsStand) -> None:
    async with ops.sf() as s:
        spammer = await author(s, ops.account_id, "spammer")
        honest = await author(s, ops.account_id, "honest")
        it = await item(s, ops.account_id, status="probation")
        for au in (spammer, spammer, honest):
            src = await source(s, ops.account_id, author_id=au)
            await evidence(
                s, ops.account_id, it, observed_at=ops.clock.now(), source_id=src, author_id=au
            )
        await s.commit()
    revoked = await block_author(ops.uow, ops.clock, "@ops_acc", "@Spammer")
    assert revoked == 2
    assert await ops.scalar("SELECT trust FROM authors WHERE id = :i", i=spammer) == -1
    live = await ops.scalar(
        "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i AND revoked_at IS NULL", i=it
    )
    assert live == 1
    with pytest.raises(AdminError, match="нет автора"):
        await block_author(ops.uow, ops.clock, "ops_acc", "nobody")
    with pytest.raises(AdminError, match="нет аккаунта"):
        await block_author(ops.uow, ops.clock, "ghost", "spammer")


async def test_reanalyze_enqueues_failed_or_all(ops: OpsStand) -> None:
    async with ops.sf() as s:
        await source(s, ops.account_id, analysis_status="failed")
        await source(s, ops.account_id, analysis_status="analyzed")
        await s.commit()
    assert await reanalyze(ops.uow, ops.clock, "ops_acc") == 1
    assert await reanalyze(ops.uow, ops.clock, "ops_acc", everything=True) == 2
    assert await ops.scalar("SELECT count(*) FROM jobs WHERE kind = 'brain.analyze'") == 2
