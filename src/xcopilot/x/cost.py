"""Учёт стоимости X API (pay-per-use): цены, оценка, суточная дедупликация, журнал x_api_calls.

Деньги — int микродоллары. X берёт плату за ресурс (пост, профиль) один раз в сутки по UTC:
повторное чтение того же ресурса в тот же UTC-день бесплатно.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

from xcopilot.db.repos import XApiCallsRepo
from xcopilot.db.uow import DbSession

POST = "post"
USER = "user"
OWNED_POST = "owned_post"
OWNED_USER = "owned_user"

PRICES: dict[str, int] = {
    POST: 5_000,
    USER: 10_000,
    OWNED_POST: 1_000,
    OWNED_USER: 1_000,
}
"""Цена одного ресурса по виду, микродоллары."""


def estimate(kind: str, n: int) -> int:
    """Стоимость n ресурсов вида kind, микродоллары."""
    if n < 0:
        raise ValueError("n не может быть отрицательным")
    return PRICES[kind] * n


class DailyDedup:
    """Какие ресурсы уже оплачены в UTC-день (в памяти процесса).

    Ресурс — пост или профиль; свой и чужой пост с одним id — один ресурс. Хранится только
    текущий день каждого аккаунта: с новым UTC-днём прошлый забывается.
    """

    def __init__(self) -> None:
        self._seen: dict[UUID, tuple[date, set[str]]] = {}

    @staticmethod
    def _key(kind: str, id_: str) -> str:
        return f"{'user' if kind in (USER, OWNED_USER) else 'post'}:{id_}"

    def _day_set(self, account_id: UUID, day: date) -> set[str]:
        current = self._seen.get(account_id)
        if current is None or current[0] != day:
            current = (day, set())
            self._seen[account_id] = current
        return current[1]

    def new_ids(self, account_id: UUID, day: date, kind: str, ids: Iterable[str]) -> list[str]:
        """Ещё не оплаченные сегодня id (без повторов), не отмечая их."""
        seen = self._day_set(account_id, day)
        out: list[str] = []
        for id_ in ids:
            key = self._key(kind, id_)
            if key not in seen and id_ not in out:
                out.append(id_)
        return out

    def mark(self, account_id: UUID, day: date, kind: str, ids: Iterable[str]) -> list[str]:
        """Отметить оплаченными; вернуть те, что оплачены впервые."""
        fresh = self.new_ids(account_id, day, kind, ids)
        seen = self._day_set(account_id, day)
        seen.update(self._key(kind, id_) for id_ in fresh)
        return fresh


async def record(
    session: DbSession,
    account_id: UUID,
    *,
    endpoint: str,
    resources: int,
    cost_micro_usd: int,
    utc_day: date,
    http_status: int | None,
    ok: bool,
    budget_scope: str = "x_api",
) -> None:
    """Строка в x_api_calls в транзакции вызывающего."""
    await XApiCallsRepo(session, account_id).log(
        endpoint=endpoint,
        resources=resources,
        cost_micro_usd=cost_micro_usd,
        utc_day=utc_day,
        http_status=http_status,
        status="ok" if ok else "error",
        budget_scope=budget_scope,
    )
