"""Русский перевод варианта для карточки (владелец, 16.11.2026).

Оригинал публикуется на языке аккаунта, а владелец проверяет его по переводу. Перевод делает
роль `cheap` (промпт `translate.ru.v1`) после ворот и перед показом, по одному вызову на
вариант: части треда — блоки одного вызова. Перевод только для чтения; он лежит в
`drafts.lint_report["translation"]` = {"lang", "ru": [...], "hash"}: `hash` — хэш исходных
блоков, поэтому перевод устаревшего текста (после правки владельца) не показывается.
Сбой cheap не блокирует показ: карточка получает пометку «перевод недоступен».
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel

from xcopilot.db.repos import DraftsRepo, RunsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.errors import LLMError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted

log = structlog.get_logger()

PROMPT_KEY = "translate.ru.v1"
TARGET_LANG = "ru"
KEY = "translation"


class TranslationOut(BaseModel):
    items: list[str]


def source_blocks(text: str, parts: Sequence[str]) -> list[str]:
    """Что переводится: части треда по порядку или весь текст."""
    blocks = [p for p in parts if p.strip()]
    return blocks if len(blocks) > 1 else [text]


def source_hash(blocks: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(blocks), ensure_ascii=False).encode()).hexdigest()[:16]


def needs_translation(lang: str | None) -> bool:
    return bool(lang) and lang != TARGET_LANG


def stored_translation(
    lint_report: Mapping[str, Any] | None, blocks: Sequence[str]
) -> list[str] | None:
    """Сохранённый перевод, если он относится к этим блокам, иначе None."""
    saved = (lint_report or {}).get(KEY)
    if not isinstance(saved, Mapping) or saved.get("hash") != source_hash(blocks):
        return None
    ru = saved.get("ru")
    if isinstance(ru, list) and len(ru) == len(blocks) and all(isinstance(x, str) for x in ru):
        return list(ru)
    return None


class Translator:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], gateway: LLMGateway) -> None:
        self._uow = uow_factory
        self._gateway = gateway

    async def translate(self, account_id: UUID, blocks: Sequence[str]) -> list[str] | None:
        """Перевод блоков; None — cheap недоступна или ответ не сошёлся по числу блоков."""
        items = "\n\n".join(wrap_untrusted(b, f"block_{i}") for i, b in enumerate(blocks, 1))
        try:
            out = await self._gateway.generate(
                role=Role.CHEAP,
                prompt_key=PROMPT_KEY,
                variables={"items": items, "count": str(len(blocks))},
                schema=TranslationOut,
                account_id=account_id,
                purpose="translate.ru",
            )
        except (LLMError, BudgetExceeded) as exc:
            log.warning("translate_failed", error=type(exc).__name__)
            return None
        if len(out.items) != len(blocks) or not all(i.strip() for i in out.items):
            log.warning("translate_count_mismatch", got=len(out.items), want=len(blocks))
            return None
        return [i.strip() for i in out.items]

    async def ensure_run(self, account_id: UUID, run_id: UUID) -> None:
        """Перевести финальные ревизии вариантов запуска, у которых перевода ещё нет."""
        async with self._uow() as uow:
            run = await RunsRepo(uow.session, account_id).get(run_id)
            if run is None:
                return
            lang = ((run.context_pack or {}).get("task") or {}).get("lang")
            if not needs_translation(lang):
                return
            finals: dict[int, Any] = {}
            for d in await DraftsRepo(uow.session, account_id).list_by_run(run_id):
                if d.variant_no not in finals or d.revision > finals[d.variant_no].revision:
                    finals[d.variant_no] = d
            todo = [
                (d.id, blocks)
                for d in finals.values()
                if (blocks := source_blocks(d.text, list(d.parts or [])))
                and stored_translation(d.lint_report, blocks) is None
            ]
        for draft_id, blocks in todo:
            ru = await self.translate(account_id, blocks)
            if ru is not None:
                await self._save(account_id, draft_id, blocks, ru)

    async def refresh(
        self, account_id: UUID, draft_id: UUID, text: str, parts: Sequence[str]
    ) -> list[str] | None:
        """Перевод правки владельца: обновляет сохранённый перевод; None — не нужен или не вышло."""
        async with self._uow() as uow:
            row = await DraftsRepo(uow.session, account_id).require(draft_id)
            run = await RunsRepo(uow.session, account_id).get(row.run_id)
        lang = ((run.context_pack if run else None) or {}).get("task", {}).get("lang")
        if not needs_translation(lang):
            return None
        blocks = source_blocks(text, parts)
        ru = await self.translate(account_id, blocks)
        if ru is not None:
            await self._save(account_id, draft_id, blocks, ru)
        return ru

    async def _save(
        self, account_id: UUID, draft_id: UUID, blocks: Sequence[str], ru: list[str]
    ) -> None:
        async with self._uow() as uow:
            drafts = DraftsRepo(uow.session, account_id)
            row = await drafts.require(draft_id)
            report = dict(row.lint_report or {})
            report[KEY] = {"lang": TARGET_LANG, "ru": ru, "hash": source_hash(blocks)}
            await drafts.update(draft_id, lint_report=report)
