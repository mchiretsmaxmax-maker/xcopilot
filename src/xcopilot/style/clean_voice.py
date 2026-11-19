"""«Чистый голос» (feat/clean-voice): свои посты без AI-шаблонов — материал голоса.

Пост с шаблоном (детектор `ai_slop`, `config/ai_slop.toml`) не идёт в паспорт и в примеры
голоса писателю, но остаётся своим (stance = own): цифры, исходы и приёмы из него учатся.
Решение владельца 19.11: фильтр важнее его оборотов, исключений нет. Проверяется весь текст
источника; язык угадывается по тексту.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from xcopilot.db.repos.style_samples import OwnSourceText, StyleSamplesRepo
from xcopilot.db.uow import DbSession
from xcopilot.quality.lint.slop import SlopHit, detect_slop

OwnDoc = OwnSourceText


@dataclass(frozen=True)
class VoiceFilter:
    """Свои источники, разделённые детектором: чистые и с находками (что и где)."""

    clean: list[OwnDoc] = field(default_factory=list)
    dirty: list[tuple[OwnDoc, list[SlopHit]]] = field(default_factory=list)

    @property
    def clean_ids(self) -> set[UUID]:
        return {d.source_id for d in self.clean}

    @property
    def dirty_ids(self) -> set[UUID]:
        return {d.source_id for d, _ in self.dirty}


def split_voice(docs: list[OwnDoc]) -> VoiceFilter:
    out = VoiceFilter()
    for doc in docs:
        hits = detect_slop(doc.text, None)
        if hits:
            out.dirty.append((doc, hits))
        else:
            out.clean.append(doc)
    return out


async def voice_filter(session: DbSession, account_id: UUID) -> VoiceFilter:
    """Все свои источники аккаунта через детектор AI-шаблонов."""
    return split_voice(await StyleSamplesRepo(session, account_id).own_source_texts())


async def slop_source_ids(session: DbSession, account_id: UUID) -> tuple[UUID, ...]:
    """Свои источники с AI-шаблонами, по порядку id (для фильтра поиска примеров)."""
    return tuple(sorted((await voice_filter(session, account_id)).dirty_ids))
