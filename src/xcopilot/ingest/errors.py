"""Ошибки ингеста: у каждой — понятное пользователю сообщение."""

from __future__ import annotations

from xcopilot.errors import AppError


class IngestError(AppError):
    """Ошибка ингеста; `user_message` можно показать владельцу."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class InputRejected(IngestError):
    """Вход не принят: превышен лимит, неподдерживаемый формат, пусто."""


class ParseError(IngestError):
    """Файл не удалось разобрать."""


class NoTextLayer(ParseError):
    """В PDF нет текстового слоя (скан)."""
