"""Иерархия исключений приложения."""


class AppError(Exception):
    """База всех ошибок приложения."""


class ConfigError(AppError):
    """Неверная конфигурация."""


class BudgetExceeded(AppError):
    """Превышен дневной лимит расходов."""


class ExternalServiceError(AppError):
    """Внешний сервис недоступен или вернул ошибку."""


class NotFound(AppError):
    """Сущность не найдена."""


class Forbidden(AppError):
    """Действие запрещено."""
