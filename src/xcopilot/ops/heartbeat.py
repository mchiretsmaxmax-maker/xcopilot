"""Внешний сторож: пульс на адрес службы-сторожа (аудит I, ADR 0030). Заглушка до кода."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import httpx

from xcopilot.clock import Clock


class Probe(Protocol):
    last_tick_at: datetime | None


class WorkerProbe(Probe, Protocol):
    busy_since: datetime | None


class Heartbeat:
    last_ok_at: datetime | None

    def __init__(
        self,
        http: httpx.AsyncClient,
        clock: Clock,
        url: str,
        *,
        scheduler: Probe,
        workers: Sequence[WorkerProbe],
        interval: float = 60.0,
    ) -> None:
        raise NotImplementedError

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    def healthy(self) -> bool:
        raise NotImplementedError

    async def beat(self) -> None:
        raise NotImplementedError

    async def run(self, stop: asyncio.Event) -> None:
        raise NotImplementedError
