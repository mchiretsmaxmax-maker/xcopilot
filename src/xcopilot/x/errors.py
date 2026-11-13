"""Ошибки X-клиента. Тексты не содержат токенов и секретов."""

from xcopilot.errors import AppError, ExternalServiceError, Forbidden, NotFound


class XError(AppError):
    """База ошибок X."""


class XNotFound(XError, NotFound):
    """Пост или пользователь не найден (удалён, приватный, неверный id)."""


class XUnavailable(XError, ExternalServiceError):
    """X или oEmbed недоступен / вернул неожиданный ответ."""


class XModeError(XError, Forbidden):
    """Действие недоступно в текущем режиме доступа к X."""


class XAuthError(XError):
    """Авторизация X не настроена, отозвана или не удалась."""


class XStateMismatch(XAuthError):
    """state в адресе возврата не совпал с выданным (возможна подмена)."""
