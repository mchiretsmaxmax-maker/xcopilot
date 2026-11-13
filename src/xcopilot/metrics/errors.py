"""Ошибки подсистемы метрик."""

from xcopilot.errors import AppError


class MetricsError(AppError):
    """База ошибок метрик."""


class UnknownEventType(MetricsError, ValueError):
    """Тип события не входит в EventType."""


class InvalidEventProps(MetricsError, ValueError):
    """props события не соответствуют схеме типа."""


class CatalogError(MetricsError):
    """Файл определения метрики разобрать нельзя или он неверен."""


class FormulaVersionError(CatalogError):
    """Формула изменилась, а версия не повышена."""


class FormulaExecutionError(MetricsError):
    """Формула не выполнилась (в том числе попытка записи в READ ONLY) или вернула не то."""
