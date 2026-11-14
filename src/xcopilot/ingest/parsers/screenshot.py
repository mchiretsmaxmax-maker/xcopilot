"""Чтение скриншота поста X ролью cheap с vision (ТЗ v3.1, этап 7): `ScreenshotRead`.

Картинка уходит в модель отдельной частью сообщения, подпись владельца — в обёртке
недоверенных данных; промпт требует считать весь текст картинки данными. Ответ —
строго по схеме `ScreenshotRead` (лишние поля запрещены, длины ограничены).
Чтение спрятано за `ScreenshotReader`: если vision у модели нет, ставится
`DisabledScreenshotReader`, и владелец получает понятный отказ.
"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID

from xcopilot.brain.schemas import Counter, ScreenshotRead
from xcopilot.ingest.errors import IngestError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.providers.base import Image
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted

PROMPT_KEY = "ingest.screenshot"
LOW_CONFIDENCE = 0.8
COUNTERS = ("views", "likes", "replies", "reposts", "bookmarks")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_SHOWN = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(k|m|тыс\.?|млн\.?)?\s*$", re.I)
_MULT = {"k": 1_000, "тыс": 1_000, "m": 1_000_000, "млн": 1_000_000}


class ScreenshotUnavailable(IngestError):
    """Чтение скриншотов выключено или модель не умеет картинки."""


class ScreenshotReader(Protocol):
    async def read_screenshot(
        self, image: bytes, note: str | None, *, account_id: UUID, mime: str = "image/png"
    ) -> ScreenshotRead: ...


class GatewayScreenshotReader:
    """Чтение через шлюз: роль cheap, картинка отдельной частью сообщения."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def read_screenshot(
        self, image: bytes, note: str | None, *, account_id: UUID, mime: str = "image/png"
    ) -> ScreenshotRead:
        if len(image) > MAX_IMAGE_BYTES:
            raise ScreenshotUnavailable("Картинка слишком большая. Пришли скрин поменьше.")
        return await self._gateway.generate(
            role=Role.CHEAP,
            prompt_key=PROMPT_KEY,
            variables={"note": wrap_untrusted(note or "—", "owner_note")},
            schema=ScreenshotRead,
            account_id=account_id,
            purpose=PROMPT_KEY,
            images=(Image(data=image, mime=mime),),
        )


class DisabledScreenshotReader:
    """Чтение выключено: понятный отказ владельцу."""

    async def read_screenshot(
        self, image: bytes, note: str | None, *, account_id: UUID, mime: str = "image/png"
    ) -> ScreenshotRead:
        raise ScreenshotUnavailable("Скриншоты пока не читаю. Пришли текст поста или ссылку.")


def parse_shown(shown: str | None) -> int | None:
    """«1.2K» → 1200, «340» → 340, «1,5 тыс.» → 1500; непонятное — None."""
    if not shown:
        return None
    match = _SHOWN.match(shown.replace(" ", " "))
    if match is None:
        return None
    number = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").lower().rstrip(".")
    return round(number * _MULT.get(suffix, 1))


def alternatives(counter: Counter) -> list[tuple[str, int]]:
    """Варианты прочтения сомнительного счётчика: как прочитано и с другой запятой."""
    shown = (counter.as_shown or "").strip()
    out: list[tuple[str, int]] = []
    first = parse_shown(shown)
    if first is not None:
        out.append((shown, first))
    if "." in shown or "," in shown:
        other = re.sub(r"[.,]", "", shown, count=1)
    else:
        digits = re.match(r"^(\d)(\d+)(.*)$", shown)
        other = f"{digits.group(1)}.{digits.group(2)}{digits.group(3)}" if digits else ""
    second = parse_shown(other)
    if second is not None and second != first:
        out.append((other, second))
    if counter.value is not None and all(v != counter.value for _, v in out):
        out.append((str(counter.value), counter.value))
    return out


def doubtful(read: ScreenshotRead) -> list[tuple[str, Counter]]:
    """Счётчики, в которых модель не уверена (ниже LOW_CONFIDENCE)."""
    out: list[tuple[str, Counter]] = []
    for name in COUNTERS:
        counter = getattr(read, name)
        if isinstance(counter, Counter) and counter.confidence < LOW_CONFIDENCE:
            out.append((name, counter))
    return out
