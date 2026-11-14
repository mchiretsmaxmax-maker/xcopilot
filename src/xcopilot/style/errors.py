"""Ошибки паспорта стиля."""

from xcopilot.errors import AppError


class StyleError(AppError):
    """База ошибок паспорта стиля."""


class NoOwnPosts(StyleError):
    """У аккаунта нет проиндексированных собственных постов: паспорт собирать не из чего."""


class NothingToRollback(StyleError):
    """Нет архивной версии, к которой можно откатиться."""


class UnknownRule(StyleError):
    """Нет такого жёсткого правила или номера правила."""
