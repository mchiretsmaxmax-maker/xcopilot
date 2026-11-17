"""Одно действие за раз (fix/one-action).

Каждое сообщение бота с inline-кнопками запоминается в служебных данных чата (`_ui`):
так ловится всё — ответы хэндлеров и карточки воркера (тот же Bot в том же процессе).
Новое действие снимает эти кнопки, чистит состояние и коротко сообщает «Закрыл «…»».
Кнопка, оставшаяся на закрытом сообщении, отвечает «Это уже закрыто».
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import InlineKeyboardMarkup, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.commands import COMMANDS
from xcopilot.bot.storage import PgStorage
from xcopilot.db.repos.review import detach_card_messages
from xcopilot.db.uow import UnitOfWork

log = structlog.get_logger()

CANCEL_CB = "act:cancel"
NAV_PREFIXES = ("menu:", "more:")  # навигация: кнопки снимаются молча
# не учитываются: прогресс уже запущенной задачи и системные предупреждения о бюджете
UNTRACKED_PREFIXES = ("pcancel:", "budget:")
CARD_PREFIX = "c1:"
CLOSED_KEEP = 200  # сколько закрытых сообщений помнить на чат

NAV, STEP, CARD = "nav", "step", "card"


def _callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def kind_of(markup: InlineKeyboardMarkup) -> str | None:
    """Вид сообщения по его кнопкам; None — не учитывать."""
    callbacks = _callbacks(markup)
    if any(c.startswith(UNTRACKED_PREFIXES) for c in callbacks):
        return None
    if any(c.startswith(CARD_PREFIX) for c in callbacks):
        return CARD
    if callbacks and all(c.startswith(NAV_PREFIXES) for c in callbacks):
        return NAV
    return STEP


def _labels() -> dict[str, str]:
    """Подписи действий для «Закрыл «…»»: callback меню и команды → понятное имя."""
    labels = {
        "menu:write": keyboards.BTN_POST,
        "menu:replies": keyboards.BTN_REPLIES,
        "menu:topics": keyboards.BTN_TOPICS,
        "menu:analytics": keyboards.BTN_STATS,
        "menu:settings": "⚙️ Настройки",
        "/start": "🚀 Старт",
        "/menu": "Меню",
    }
    for row in keyboards.more_menu().inline_keyboard:
        for b in row:
            if b.callback_data:
                labels[b.callback_data] = b.text
    for command, description in COMMANDS:
        labels[f"/{command}"] = description
    return labels


LABELS = _labels()


def label_for(trigger: str) -> str:
    if trigger.startswith("/"):
        trigger = trigger.split()[0].split("@")[0]
    return LABELS.get(trigger, trigger)


def key_for(bot: Bot, chat_id: int) -> StorageKey:
    """Ключ хранилища личного чата (чат = пользователь)."""
    return StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=chat_id)


async def strip(bot: Bot, chat_id: int, message_id: int) -> None:
    """Снять клавиатуру; «message not modified», «can't be edited» и прочее — глотать."""
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except TelegramAPIError as exc:
        log.info("one_action.strip_failed", message_id=message_id, error=str(exc)[:100])


@dataclass(frozen=True)
class Closed:
    opened: bool  # было ли что закрывать (шаг ввода или кнопки действия)
    label: str | None
    drafts: bool  # среди снятых — карточки черновиков

    def note(self) -> str | None:
        if not self.opened:
            return None
        return texts.action_closed(self.label or texts.PREV_ACTION, self.drafts)


class ActionTracker:
    """Учёт кнопок текущего действия чата и закрытие действия."""

    def __init__(self, storage: PgStorage, uow_factory: Callable[[], UnitOfWork]) -> None:
        self.storage = storage
        self._uow = uow_factory

    async def track(self, key: StorageKey, message_id: int, kind: str) -> None:
        ui = await self.storage.get_ui(key)
        msgs = [m for m in ui.get("msgs", []) if m[0] != message_id]
        msgs.append([message_id, kind])
        await self.storage.set_ui(key, {**ui, "msgs": msgs})

    async def is_closed(self, key: StorageKey, message_id: int) -> bool:
        ui = await self.storage.get_ui(key)
        return message_id in ui.get("closed", [])

    async def close(self, bot: Bot, key: StorageKey, new_action: str | None = None) -> Closed:
        """Снять кнопки прошлого действия, очистить состояние, запомнить новое действие."""
        ui = await self.storage.get_ui(key)
        state = await self.storage.get_state(key)
        msgs: list[list[Any]] = list(ui.get("msgs", []))
        for message_id, _ in msgs:
            await strip(bot, key.chat_id, int(message_id))
        cards = [int(m) for m, kind in msgs if kind == CARD]
        if cards:
            async with self._uow() as uow:
                await detach_card_messages(uow.session, key.chat_id, cards)
        if state is not None:
            await self.storage.set_state(key, None)
        await self.storage.set_data(key, {})
        closed = [*ui.get("closed", []), *(int(m) for m, _ in msgs)][-CLOSED_KEEP:]
        await self.storage.set_ui(key, {"msgs": [], "closed": closed, "action": new_action})
        opened = state is not None or any(kind != NAV for _, kind in msgs)
        return Closed(opened, ui.get("action"), bool(cards))


class TrackKeyboards(BaseRequestMiddleware):
    """Middleware сессии Bot: запомнить каждое отправленное сообщение с inline-кнопками."""

    def __init__(self, tracker: ActionTracker) -> None:
        self.tracker = tracker

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        response = await make_request(bot, method)
        if isinstance(method, SendMessage) and isinstance(
            method.reply_markup, InlineKeyboardMarkup
        ):
            result: Any = response  # цепочка сессии отдаёт уже разобранный результат
            kind = kind_of(method.reply_markup)
            if kind is not None and isinstance(result, Message) and result.chat.type == "private":
                try:
                    await self.tracker.track(key_for(bot, result.chat.id), result.message_id, kind)
                except Exception:  # учёт не должен ломать отправку
                    log.warning("one_action.track_failed", exc_info=True)
        return response


def install(bot: Bot, tracker: ActionTracker) -> None:
    """Подключить учёт к сессии Bot один раз (повторная сборка диспетчера — заменить трекер)."""
    for mw in bot.session.middleware:
        if isinstance(mw, TrackKeyboards):
            mw.tracker = tracker
            return
    bot.session.middleware(TrackKeyboards(tracker))
