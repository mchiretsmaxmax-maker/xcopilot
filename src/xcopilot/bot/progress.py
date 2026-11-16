"""Статус-сообщение долгой задачи: одно сообщение, правится по этапам.

Бот присылает его при постановке задачи (`begin`), обработчики задач двигают этапы
(`step`, `done`, `fail`). Состояние — в памяти процесса: бот и воркер живут в одном
процессе; после рестарта старое сообщение просто перестаёт обновляться. Правка
пропускается, если текст не изменился (Telegram иначе отвечает «message is not modified»).
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, tzinfo
from typing import Protocol
from uuid import UUID

import structlog

from xcopilot.clock import Clock

log = structlog.get_logger()

Stage = tuple[str, str, str]  # (ключ, значок, название)

QUEUED: Stage = ("queued", "⏳", "Задача принята")
DONE_CARDS: Stage = ("done", "✅", "Готово, карточки ниже")

FLOWS: dict[str, tuple[Stage, ...]] = {
    "post": (
        QUEUED,
        ("write", "✍️", "Пишу 3 варианта"),
        ("gate", "🛡", "Проверяю качество"),
        DONE_CARDS,
    ),
    "topics": (QUEUED, ("topics", "💡", "Подбираю темы"), ("done", "✅", "Готово, темы ниже")),
    "material": (
        QUEUED,
        ("read", "📥", "Читаю материал"),
        ("analyze", "🧠", "Разбираю"),
        ("done", "✅", "Готово"),
    ),
}
MAX_RUNS = 200
SOURCE_PREFIX = "source:"


class Messenger(Protocol):
    async def send_status(self, account_id: UUID, text: str) -> list[tuple[int, int]]: ...

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None: ...


TzFor = Callable[[UUID], Awaitable[tzinfo]]


@dataclass
class _Run:
    account_id: UUID
    flow: tuple[Stage, ...]
    targets: list[tuple[int, int]]
    tz: tzinfo
    reached: list[tuple[Stage, str]] = field(default_factory=list)
    last_text: str = ""


class ProgressBoard:
    def __init__(self, messenger: Messenger, clock: Clock, tz_for: TzFor | None = None) -> None:
        self._messenger = messenger
        self._clock = clock
        self._tz_for = tz_for
        self._runs: OrderedDict[str, _Run] = OrderedDict()

    async def begin(self, account_id: UUID, flow: str, key: str | None = None) -> str | None:
        """Прислать статус-сообщение; вернуть ключ для задачи (None — не удалось)."""
        stages = FLOWS[flow]
        tz: tzinfo = UTC
        if self._tz_for is not None:
            try:
                tz = await self._tz_for(account_id)
            except Exception:
                log.warning("progress_tz_failed", exc_info=True)
        run = _Run(account_id, stages, [], tz)
        self._mark(run, stages[0])
        try:
            targets = await self._messenger.send_status(account_id, self._render(run))
        except Exception:
            log.warning("progress_send_failed", exc_info=True)
            return None
        if not targets:
            return None
        run.targets = targets
        run.last_text = self._render(run)
        key = key or uuid.uuid4().hex
        self._runs[key] = run
        while len(self._runs) > MAX_RUNS:
            self._runs.popitem(last=False)
        return key

    async def step(self, key: str | None, stage: str) -> None:
        run = self._runs.get(key) if key else None
        if run is None:
            return
        wanted = next((s for s in run.flow if s[0] == stage), None)
        if wanted is None or (run.reached and run.reached[-1][0][0] == stage):
            return
        self._mark(run, wanted)
        await self._push(run)

    async def done(self, key: str | None) -> None:
        run = self._runs.get(key) if key else None
        if run is None:
            return
        await self.step(key, run.flow[-1][0])
        self._runs.pop(key, None)  # type: ignore[arg-type]

    async def fail(self, key: str | None, reason: str) -> None:
        run = self._runs.pop(key, None) if key else None
        if run is None:
            return
        current = run.reached[-1][0] if run.reached else run.flow[0]
        run.reached = run.reached[:-1]
        text = self._render(run, finished=True) + f"\n❌ {current[2]}: {reason}"
        await self._edit(run, text)

    def _mark(self, run: _Run, stage: Stage) -> None:
        run.reached.append((stage, self._clock.now().astimezone(run.tz).strftime("%H:%M:%S")))

    def _render(self, run: _Run, *, finished: bool = False) -> str:
        lines = []
        for i, (stage, at) in enumerate(run.reached):
            last = i == len(run.reached) - 1 and not finished
            icon = stage[1] if last else "✔️"
            lines.append(f"{icon} {stage[2]} — {at}")
        return "\n".join(lines)

    async def _push(self, run: _Run) -> None:
        await self._edit(run, self._render(run))

    async def _edit(self, run: _Run, text: str) -> None:
        if text == run.last_text:
            return
        run.last_text = text
        for chat_id, message_id in run.targets:
            await self._messenger.edit_text(chat_id, message_id, text)


class ProgressSink(Protocol):
    """Что нужно обработчикам задач (writer, ingest, brain): двигать этапы статуса."""

    async def step(self, key: str | None, stage: str) -> None: ...

    async def done(self, key: str | None) -> None: ...

    async def fail(self, key: str | None, reason: str) -> None: ...
