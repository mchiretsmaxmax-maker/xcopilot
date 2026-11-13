"""Обучение: владелец присылает тексты, ссылки, @аккаунты и файлы → ингест.

Роутер подключается последним: ловит сообщения вне сценариев (без состояния FSM).
"""

from __future__ import annotations

import io
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.ingest.service import FilePayload, IngestPayload, IngestReceipt, file_precheck


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="learn")

    async def _reply(message: Message, receipt: IngestReceipt) -> None:
        await message.answer(receipt.message, reply_markup=receipt.keyboard)

    @router.message(StateFilter(None), F.document)
    async def on_file(
        message: Message,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        document = message.document
        if document is None:
            return
        if acc is None or deps.ingest is None:
            await message.answer(texts.INGEST_NO_ACCOUNT)
            return
        await rec("ingest.accept")
        name = document.file_name or "file"
        refusal = file_precheck(name, document.file_size)
        if refusal is not None:
            await message.answer(refusal)
            return
        buf = io.BytesIO()
        await deps.bot.download(document.file_id, destination=buf)
        receipt = await deps.ingest.accept(
            acc.id,
            user_id,
            IngestPayload(
                file=FilePayload(name, buf.getvalue()),
                file_name=name,
                file_size=document.file_size,
                owner_tg_id=message.from_user.id if message.from_user else None,
            ),
        )
        await _reply(message, receipt)

    @router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
    async def on_text(
        message: Message,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        if acc is None or deps.ingest is None:
            await message.answer(texts.INGEST_NO_ACCOUNT)
            return
        await rec("ingest.accept")
        receipt = await deps.ingest.accept(
            acc.id,
            user_id,
            IngestPayload(
                text=message.text,
                owner_tg_id=message.from_user.id if message.from_user else None,
            ),
        )
        await _reply(message, receipt)

    @router.callback_query(F.data.startswith("ikind:"))
    async def on_kind(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parts = (query.data or "").split(":")
        if message is None or acc is None or deps.ingest is None or len(parts) != 3:
            return
        try:
            batch = UUID(parts[1])
            await deps.ingest.set_kind(acc.id, batch, parts[2])
        except ValueError:
            return
        await rec("ingest.kind")
        await message.answer(texts.ingest_kind_set(parts[2]))

    @router.callback_query(F.data == "ingest:text")
    async def on_send_text(query: CallbackQuery, rec: Recorder) -> None:
        await query.answer()
        await rec("ingest.send_text")
        if isinstance(query.message, Message):
            await query.message.answer(texts.INGEST_SEND_TEXT)

    return router
