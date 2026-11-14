"""Обучение: владелец присылает тексты, ссылки, @аккаунты, файлы, скрины и пересылки → ингест.

Роутер подключается последним: ловит сообщения вне сценариев (без состояния FSM).
ТЗ v3.1: если в зависимостях есть `ingest_batch` (Debouncer), сообщения одного чата за окно
(альбом или серия) уходят в ингест одной пачкой и получают один ответ.
"""

from __future__ import annotations

import io
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import (
    CallbackQuery,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
)

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps, IngestBatchKey
from xcopilot.ingest.batch import Debouncer
from xcopilot.ingest.service import (
    FilePayload,
    ForwardInfo,
    ImagePayload,
    IngestPayload,
    IngestReceipt,
    file_precheck,
)

IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp")


def forward_info(message: Message) -> ForwardInfo | None:
    """Автор пересланного сообщения Telegram (ТЗ v3.1) или None."""
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        return ForwardInfo(name=user.full_name, username=user.username)
    if isinstance(origin, MessageOriginHiddenUser):
        return ForwardInfo(name=origin.sender_user_name)
    if isinstance(origin, MessageOriginChannel):
        chat = origin.chat
        name = origin.author_signature or chat.title
        return ForwardInfo(name=name, username=chat.username, chat_title=chat.title)
    if isinstance(origin, MessageOriginChat):
        chat = origin.sender_chat
        return ForwardInfo(name=chat.title, username=chat.username, chat_title=chat.title)
    return None


def build_ingest_debouncer(
    deps: BotDeps, *, window_s: float = 8.0
) -> Debouncer[IngestBatchKey, IngestPayload]:
    """Пачки для роутера: по окончании окна — accept_batch и один ответ в чат."""

    async def flush(key: IngestBatchKey, items: list[IngestPayload]) -> None:
        chat_id, account_id, user_id = key
        if deps.ingest is None:
            return
        receipt = await deps.ingest.accept_batch(account_id, user_id, items)
        await deps.bot.send_message(chat_id, receipt.message, reply_markup=receipt.keyboard)

    return Debouncer(flush, window_s=window_s)


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="learn")

    async def _reply(message: Message, receipt: IngestReceipt) -> None:
        await message.answer(receipt.message, reply_markup=receipt.keyboard)

    async def _submit(
        message: Message, deps: BotDeps, acc: AccountCtx, user_id: UUID, payload: IngestPayload
    ) -> None:
        assert deps.ingest is not None
        if deps.ingest_batch is not None:
            await deps.ingest_batch.add((message.chat.id, acc.id, user_id), payload)
            return
        await _reply(message, await deps.ingest.accept(acc.id, user_id, payload))

    async def _download(deps: BotDeps, file_id: str) -> bytes:
        buf = io.BytesIO()
        await deps.bot.download(file_id, destination=buf)
        return buf.getvalue()

    def _owner(message: Message) -> int | None:
        return message.from_user.id if message.from_user else None

    @router.message(StateFilter(None), F.photo)
    async def on_photo(
        message: Message,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        if acc is None or deps.ingest is None:
            await message.answer(texts.INGEST_NO_ACCOUNT)
            return
        photos = message.photo or []
        if not photos:
            return
        await rec("ingest.accept")
        data = await _download(deps, photos[-1].file_id)  # самый крупный размер — последний
        payload = IngestPayload(
            images=(ImagePayload(data=data, mime="image/jpeg"),),
            note=message.caption,
            owner_tg_id=_owner(message),
            forward=forward_info(message),
        )
        await _submit(message, deps, acc, user_id, payload)

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
        if document.mime_type in IMAGE_MIMES:
            data = await _download(deps, document.file_id)
            payload = IngestPayload(
                images=(ImagePayload(data=data, mime=document.mime_type),),
                note=message.caption,
                owner_tg_id=_owner(message),
            )
            await _submit(message, deps, acc, user_id, payload)
            return
        name = document.file_name or "file"
        refusal = file_precheck(name, document.file_size)
        if refusal is not None:
            await message.answer(refusal)
            return
        data = await _download(deps, document.file_id)
        payload = IngestPayload(
            file=FilePayload(name, data),
            file_name=name,
            file_size=document.file_size,
            owner_tg_id=_owner(message),
        )
        await _submit(message, deps, acc, user_id, payload)

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
        payload = IngestPayload(
            text=message.text, owner_tg_id=_owner(message), forward=forward_info(message)
        )
        await _submit(message, deps, acc, user_id, payload)

    @router.callback_query(F.data.startswith("ikind:"))
    async def on_kind(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        """Кнопки «Это что?» v3 в старых сообщениях."""
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

    @router.callback_query(F.data.startswith("irel:"))
    async def on_relation(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parts = (query.data or "").split(":")
        if message is None or acc is None or deps.ingest is None or len(parts) != 3:
            return
        try:
            await deps.ingest.set_stance(acc.id, UUID(parts[1]), parts[2])
        except ValueError:
            return
        await rec("ingest.stance")
        await message.answer(texts.ingest_stance_set(parts[2]))

    @router.callback_query(F.data.startswith("irm:") | F.data.startswith("irmb:"))
    async def on_remove(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, user_id: UUID, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        prefix, _, raw = (query.data or "").partition(":")
        if message is None or acc is None or deps.ingest is None:
            return
        try:
            target = UUID(raw)
        except ValueError:
            return
        if prefix == "irmb":
            removed = await deps.ingest.remove_batch(acc.id, target, user_id)
        else:
            removed = await deps.ingest.remove(target, user_id, account_id=acc.id)
        await rec("ingest.remove")
        await message.answer(texts.ingest_removed(removed))

    @router.callback_query(F.data.startswith("iscr:"))
    async def on_counter(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, user_id: UUID, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parts = (query.data or "").split(":")
        if message is None or acc is None or deps.ingest is None or len(parts) != 4:
            return
        _, raw_id, name, raw_value = parts
        try:
            source_id = UUID(raw_id)
            value = None if raw_value == "skip" else int(raw_value)
            await deps.ingest.confirm_counter(acc.id, source_id, name, value, user_id=user_id)
        except ValueError:
            return
        await rec("ingest.counter")
        if value is None:
            await message.answer(texts.SCREENSHOT_COUNTER_SKIPPED)
        else:
            await message.answer(texts.screenshot_counter_set(name, value))

    @router.callback_query(F.data.startswith("iscrt:"))
    async def on_screenshot_text(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        _, _, raw = (query.data or "").partition(":")
        if message is None or acc is None or deps.ingest is None:
            return
        try:
            ok = await deps.ingest.screenshot_as_text(acc.id, UUID(raw))
        except ValueError:
            return
        await rec("ingest.as_text")
        await message.answer(texts.SCREENSHOT_AS_TEXT if ok else texts.SCREENSHOT_AS_TEXT_GONE)

    @router.callback_query(F.data == "ingest:text")
    async def on_send_text(query: CallbackQuery, rec: Recorder) -> None:
        await query.answer()
        await rec("ingest.send_text")
        if isinstance(query.message, Message):
            await query.message.answer(texts.INGEST_SEND_TEXT)

    return router
