"""Стенд сквозных тестов этапа 15: собранное приложение (`build_app`) на подделках.

Настоящие: PostgreSQL, диспетчер бота, воркер, очередь, сервисы всех этапов.
Подделки: шлюз моделей (ответ по ключу промпта), X, Telegram, эмбеддер, часы.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.telegram import SentMessage
from tests.fakes.x import FakeX
from tests.integration.bot.conftest import Client
from xcopilot.app import App

SAMPLES = [
    "блобы снизили комиссии роллапов втрое, проверил на своих транзакциях за неделю",
    "перенёс кошелёк на l2 и забыл про газ, мосты теперь скучные и тихие",
    "старый мост закрылся без шума, ликвидность ушла в новые сети за сутки",
    "12 провалов за год научили меня не торопиться с аирдропами",
    "считаю газ каждую пятницу: в этом месяце потратил меньше, чем на кофе",
]
MATERIALS = [
    "12 провалов за год. Вот что я понял про l2. А вы?",
    "37 транзакций за неделю и ни одной дороже цента. Блобы работают.",
    "3 моста закрылись за месяц. Ликвидность переехала. Что дальше?",
]
EDITED = "блобы снизили комиссии роллапов втрое, и это видно по моим транзакциям за неделю"
HOOK = "Личная цифра в первой строке даёт больше ответов"
SPAM = "Пиши КАПСОМ и обещай иксы в каждом посте"


@dataclass
class MvpStand:
    app: App
    telegram: FakeTelegram
    clock: FakeClock
    x: FakeX
    owner: Client

    @property
    def account_id(self) -> UUID:
        raise NotImplementedError

    async def drain(self) -> int:
        raise NotImplementedError

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        raise NotImplementedError

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        raise NotImplementedError

    async def run_job(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    async def onboard(self, samples: list[str]) -> None:
        raise NotImplementedError

    async def send_material(self, body: str, *, author: str | None = None) -> None:
        raise NotImplementedError

    async def build_passport(self) -> None:
        raise NotImplementedError

    async def request_topics(self) -> None:
        raise NotImplementedError

    async def write_first_topic(self) -> None:
        raise NotImplementedError

    async def write_topic(self, topic: str) -> None:
        raise NotImplementedError

    def cards(self) -> list[SentMessage]:
        raise NotImplementedError

    def own_post(self, post_id: str, body: str, *, likes: int = 0, views: int = 0) -> None:
        raise NotImplementedError

    async def extract_knowledge(
        self, statement: str, *, materials: list[str] | None = None
    ) -> UUID:
        raise NotImplementedError

    async def run_nightly(self) -> None:
        raise NotImplementedError

    async def item_status(self, item_id: UUID) -> str:
        raise NotImplementedError

    async def item_weights(self, item_id: UUID) -> dict[str, float]:
        raise NotImplementedError

    async def probation_draft(self, item_id: UUID) -> UUID:
        raise NotImplementedError

    async def show_variant(self, draft_id: UUID) -> SentMessage:
        raise NotImplementedError

    async def draft_text(self, draft_id: UUID) -> str:
        raise NotImplementedError

    async def pack_items(self, topic: str) -> list[UUID]:
        raise NotImplementedError

    async def rollback_from_bot(self, *, days_back: int) -> None:
        raise NotImplementedError


async def make_mvp_stand(db_url: str, telegram: FakeTelegram, tmp_path: Any) -> MvpStand:
    raise NotImplementedError
