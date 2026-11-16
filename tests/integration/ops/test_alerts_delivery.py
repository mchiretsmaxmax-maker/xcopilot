"""Аудит D (AUD-004): алерт, не дошедший до владельца, не отмечается отправленным."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from tests.integration.ops.conftest import OpsStand
from tests.integration.ops.test_alerts import dead_job, mark_backup
from xcopilot.ops.alerts import send_alerts


@dataclass
class FlakyInbox:
    """Уведомитель: первые `down` вызовов никому не доставлены (0), дальше — доставлены."""

    down: int = 1
    sent: list[str] = field(default_factory=list)

    async def notify(
        self, account_id: UUID, text: str, keyboard: Any = None, roles: tuple[str, ...] = ("owner",)
    ) -> int:
        if self.down > 0:
            self.down -= 1
            return 0
        self.sent.append(text)
        return 1

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        await self.notify(account_id, text)


async def test_alert_not_marked_when_delivery_failed(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    await dead_job(ops)
    inbox = FlakyInbox(down=1)
    assert await send_alerts(ops.deps, ops.account_id, inbox) == []
    marks = await ops.scalar(
        "SELECT count(*) FROM ops_marks WHERE account_id = :a AND key LIKE 'alert:job_dead:%'",
        a=ops.account_id,
    )
    assert marks == 0

    again = await send_alerts(ops.deps, ops.account_id, inbox)
    assert [a.code for a in again] == ["job_dead"]
    assert len(inbox.sent) == 1
    marks = await ops.scalar(
        "SELECT count(*) FROM ops_marks WHERE account_id = :a AND key LIKE 'alert:job_dead:%'",
        a=ops.account_id,
    )
    assert marks == 1
