"""Автосверка публикаций со своей лентой (ТЗ, этап 12, задача 9).

Одобренный или исправленный черновик ищется среди своих постов в течение 48 часов после
решения: сходство от 0.85 — публикация найдена сама (`matched_by = auto`), от 0.6 до 0.85 —
вопрос владельцу, после 48 часов без совпадения — `unconfirmed`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.review.settings import ReviewSettings
from xcopilot.x.models import XPost


@dataclass(frozen=True)
class Match:
    draft_id: UUID
    outcome: Literal["auto", "ask", "unconfirmed"]
    x_post_id: str | None = None
    url: str | None = None
    score: float = 0.0


def similarity(a: str, b: str) -> float:
    """Сходство текстов 0..1 (rapidfuzz, без регистра и лишних пробелов)."""
    raise NotImplementedError


class PublicationMatcher:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings or ReviewSettings()

    async def match_publications(self, account_id: UUID, own_posts: Sequence[XPost]) -> list[Match]:
        """Сверить ждущие публикации черновики со своими постами; auto — сразу публикация."""
        raise NotImplementedError
