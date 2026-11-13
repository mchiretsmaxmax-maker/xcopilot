"""FakeTelegram: записывает исходящие вызовы Bot API вместо похода в Telegram.

`FakeTelegram.bot` — настоящий aiogram `Bot` с поддельной сессией: все методы
(sendMessage, answerCallbackQuery, …) попадают в `calls`, а sendMessage — ещё и в `sent`.
Входящие обновления собирают `message_update` и `callback_update`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    GetFile,
    GetMe,
    GetUpdates,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    File,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)

FAKE_TOKEN = "123456:FAKE-TOKEN-for-tests"
_NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@dataclass
class SentMessage:
    chat_id: int
    text: str
    reply_markup: Any = None

    @property
    def buttons(self) -> list[str]:
        """Подписи всех inline-кнопок."""
        return [b.text for row in _rows(self.reply_markup) for b in row]

    @property
    def callbacks(self) -> list[str]:
        """callback_data всех inline-кнопок."""
        return [b.callback_data or "" for row in _rows(self.reply_markup) for b in row]


def _rows(markup: Any) -> list[list[Any]]:
    if isinstance(markup, InlineKeyboardMarkup):
        return markup.inline_keyboard
    return []


class FakeSession(BaseSession):
    """Сессия Bot, которая ничего не отправляет по сети."""

    def __init__(self, owner: FakeTelegram) -> None:
        super().__init__()
        self.owner = owner

    async def close(self) -> None:
        return None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.owner.calls.append(method)
        if isinstance(method, SendMessage):
            chat_id = int(method.chat_id)
            self.owner.sent.append(SentMessage(chat_id, method.text, method.reply_markup))
            self.owner.message_counter += 1
            return Message(
                message_id=self.owner.message_counter,
                date=_NOW,
                chat=Chat(id=chat_id, type="private"),
                text=method.text,
            )
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="xcopilot", username="xcopilot_bot")
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0.01)
            return []
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, GetFile):
            data = self.owner.files.get(method.file_id, b"")
            return File(
                file_id=method.file_id,
                file_unique_id=f"u{method.file_id}",
                file_size=len(data),
                file_path=f"documents/{method.file_id}",
            )
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        file_id = url.rsplit("/", 1)[-1]
        yield self.owner.files[file_id]


@dataclass
class FakeTelegram:
    sent: list[SentMessage] = field(default_factory=list)
    calls: list[TelegramMethod[Any]] = field(default_factory=list)
    message_counter: int = 1000
    files: dict[str, bytes] = field(default_factory=dict)
    _bot: Bot | None = None

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(FAKE_TOKEN, session=FakeSession(self))
        return self._bot

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> SentMessage:
        """Прямая отправка в обход Bot (для старых тестов подделки)."""
        message = SentMessage(chat_id, text, reply_markup)
        self.sent.append(message)
        return message

    def texts_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.sent if m.chat_id == chat_id]

    def last_to(self, chat_id: int) -> SentMessage:
        return [m for m in self.sent if m.chat_id == chat_id][-1]

    def callback_answers(self) -> list[AnswerCallbackQuery]:
        return [c for c in self.calls if isinstance(c, AnswerCallbackQuery)]


def message_update(update_id: int, user_id: int, text: str) -> Update:
    """Входящее личное сообщение пользователя; команды получают entity bot_command."""
    entities = None
    if text.startswith("/"):
        length = len(text.split()[0])
        entities = [{"type": "bot_command", "offset": 0, "length": length}]
    message = Message.model_validate(
        {
            "message_id": update_id,
            "date": _NOW,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"u{user_id}"},
            "text": text,
            "entities": entities,
        }
    )
    return Update(update_id=update_id, message=message)


def callback_update(update_id: int, user_id: int, data: str) -> Update:
    """Нажатие inline-кнопки с данными data."""
    message = Message.model_validate(
        {
            "message_id": update_id,
            "date": _NOW,
            "chat": {"id": user_id, "type": "private"},
            "text": "кнопки",
        }
    )
    query = CallbackQuery.model_validate(
        {
            "id": f"cb{update_id}",
            "from": {"id": user_id, "is_bot": False, "first_name": f"u{user_id}"},
            "chat_instance": "ci",
            "data": data,
            "message": message.model_dump(by_alias=True),
        }
    )
    return Update(update_id=update_id, callback_query=query)


def document_update(
    update_id: int, user_id: int, file_name: str, file_size: int, caption: str | None = None
) -> Update:
    """Входящий файл (документ); содержимое для скачивания — в FakeTelegram.files[file_id]."""
    message = Message.model_validate(
        {
            "message_id": update_id,
            "date": _NOW,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"u{user_id}"},
            "caption": caption,
            "document": {
                "file_id": f"f{update_id}",
                "file_unique_id": f"uf{update_id}",
                "file_name": file_name,
                "file_size": file_size,
            },
        }
    )
    return Update(update_id=update_id, message=message)
