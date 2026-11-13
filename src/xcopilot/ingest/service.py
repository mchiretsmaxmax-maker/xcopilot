"""IngestService: приём входа (accept) и обработка источника (process)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.ingest.blobs import BlobStore
from xcopilot.ingest.classify import InputKind
from xcopilot.ingest.tagger import Tagger
from xcopilot.x.client import XClient

INGEST_KIND = "ingest.process"


class NotifierLike(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


XFactory = Callable[[UUID], Awaitable[XClient]]


@dataclass(frozen=True, slots=True)
class FilePayload:
    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class IngestPayload:
    """Вход от пользователя: текст или файл; owner_tg_id — для экспорта Telegram."""

    text: str | None = None
    file: FilePayload | None = None
    file_name: str | None = None
    file_size: int | None = None
    owner_tg_id: int | None = None


@dataclass
class IngestReceipt:
    kind: InputKind
    message: str
    batch_id: UUID | None = None
    source_ids: list[UUID] = field(default_factory=list)
    duplicates: int = 0
    rejected: bool = False
    offer_paid: bool = False
    keyboard: Any = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: str
    chunks: int = 0
    duplicates: int = 0


class IngestService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        tagger: Tagger,
        embedder: Embedder,
        x_for: XFactory,
        notifier: NotifierLike,
        blobs: BlobStore,
        max_attempts: int = 5,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._tagger = tagger
        self._embedder = embedder
        self._x_for = x_for
        self._notifier = notifier
        self._blobs = blobs
        self._max_attempts = max_attempts

    async def accept(
        self, account_id: UUID, user_id: UUID, payload: IngestPayload
    ) -> IngestReceipt:
        raise NotImplementedError

    async def process(self, source_id: UUID, *, account_id: UUID) -> ProcessResult:
        raise NotImplementedError

    async def set_kind(self, account_id: UUID, batch_id: UUID, kind: str) -> int:
        raise NotImplementedError
