"""Чтение скриншота поста X ролью cheap с vision (ТЗ v3.1, этап 7): `ScreenshotRead`."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from xcopilot.brain.schemas import ScreenshotRead
from xcopilot.ingest.errors import IngestError
from xcopilot.llm.gateway import LLMGateway

PROMPT_KEY = "ingest.screenshot"


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
        raise NotImplementedError


class DisabledScreenshotReader:
    """Чтение выключено: понятный отказ владельцу."""

    async def read_screenshot(
        self, image: bytes, note: str | None, *, account_id: UUID, mime: str = "image/png"
    ) -> ScreenshotRead:
        raise NotImplementedError
