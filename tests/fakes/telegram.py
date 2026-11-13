"""FakeTelegram: запоминает исходящие сообщения вместо похода в Telegram."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SentMessage:
    chat_id: int
    text: str
    reply_markup: Any = None


@dataclass
class FakeTelegram:
    sent: list[SentMessage] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> SentMessage:
        message = SentMessage(chat_id, text, reply_markup)
        self.sent.append(message)
        return message

    def texts_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.sent if m.chat_id == chat_id]
