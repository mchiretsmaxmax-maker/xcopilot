"""«Чистый голос»: свои посты без AI-шаблонов — материал паспорта и примеров голоса (заглушка)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from xcopilot.db.uow import DbSession
from xcopilot.quality.lint.slop import SlopHit


@dataclass(frozen=True)
class OwnDoc:
    source_id: UUID
    text: str
    author_handle: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class VoiceFilter:
    clean: list[OwnDoc] = field(default_factory=list)
    dirty: list[tuple[OwnDoc, list[SlopHit]]] = field(default_factory=list)

    @property
    def clean_ids(self) -> set[UUID]:
        raise NotImplementedError

    @property
    def dirty_ids(self) -> set[UUID]:
        raise NotImplementedError


async def voice_filter(session: DbSession, account_id: UUID) -> VoiceFilter:
    raise NotImplementedError
