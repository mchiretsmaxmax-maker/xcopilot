"""Ошибки шлюза моделей. Тексты ошибок не содержат тел ответов и ключей."""

from __future__ import annotations

from xcopilot.errors import AppError, ExternalServiceError


class LLMError(ExternalServiceError):
    """База ошибок вызова моделей."""


class ProviderError(LLMError):
    """Провайдер вернул ошибку или не ответил."""

    def __init__(self, provider: str, *, status: int | None = None, kind: str = "") -> None:
        self.provider = provider
        self.status = status
        self.kind = kind
        parts = [provider + ":", f"HTTP {status}" if status is not None else (kind or "ошибка")]
        super().__init__(" ".join(parts))

    def short(self) -> str:
        """Короткая метка для журнала: класс и код."""
        tail = str(self.status) if self.status is not None else self.kind
        return f"{type(self).__name__} {tail}".strip()


class TransientProviderError(ProviderError):
    """Временный сбой (429, 5xx, таймаут, обрыв): можно повторить."""


class PermanentProviderError(ProviderError):
    """Ошибка запроса (4xx кроме 429): повтор не поможет."""


class ProviderUnavailable(LLMError):
    """Провайдер недоступен после повторов или разомкнут предохранитель."""


class CircuitOpen(ProviderUnavailable):
    """Предохранитель провайдера разомкнут: вызов не выполняется."""


class LLMOutputError(LLMError):
    """Ответ модели не прошёл проверку схемы и после одной починки."""


class PromptError(AppError):
    """Ошибка шаблона промпта."""


class PromptNotFound(PromptError):
    """Промпта с таким ключом нет."""


class PromptVariableMissing(PromptError):
    """Для рендера не хватает переменных."""
