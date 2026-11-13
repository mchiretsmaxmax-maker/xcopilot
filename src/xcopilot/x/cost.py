"""Учёт стоимости X API (pay-per-use): цены, оценка, суточная дедупликация, журнал x_api_calls.

Деньги — int микродоллары. X берёт плату за ресурс (пост, профиль) один раз в сутки по UTC:
повторное чтение того же ресурса в тот же UTC-день бесплатно.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

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
    raise NotImplementedError


class DailyDedup:
    """Какие ресурсы уже оплачены в UTC-день (в памяти процесса)."""

    def new_ids(self, account_id: UUID, day: date, kind: str, ids: Iterable[str]) -> list[str]:
        """Ещё не оплаченные сегодня id (без повторов), не отмечая их."""
        raise NotImplementedError

    def mark(self, account_id: UUID, day: date, kind: str, ids: Iterable[str]) -> list[str]:
        """Отметить оплаченными; вернуть те, что оплачены впервые."""
        raise NotImplementedError


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
) -> None:
    """Строка в x_api_calls в транзакции вызывающего."""
    raise NotImplementedError
