"""Алерт дрейфа голоса: `brain_voice_drift` вырос на 25% к уровню первой недели (этап 14, п. 16)."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.repos import AnalyticsRepo
from xcopilot.db.uow import DbSession

DRIFT_GROWTH = 0.25
METRIC = "brain_voice_drift"


async def drift_alert(session: DbSession, account_id: UUID) -> str | None:
    """Текст алерта владельцу или None, если голос не уплыл (или данных меньше двух недель)."""
    values = await AnalyticsRepo(session, account_id).weekly_values(METRIC)
    if len(values) < 2 or values[0] <= 0:
        return None
    growth = values[-1] / values[0] - 1
    if growth < DRIFT_GROWTH:
        return None
    return (
        "Голос уплывает: расстояние между постами с ботом и твоими старыми постами выросло на"
        f" {round(growth * 100)}% к первой неделе. Проверь последние черновики и правила голоса"
        " в /brain."
    )
