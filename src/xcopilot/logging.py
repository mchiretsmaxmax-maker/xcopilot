"""JSON-логи со сквозным request_id и маскированием секретов."""

from __future__ import annotations

import logging as std_logging
import re
import uuid
from collections.abc import Iterable, MutableMapping
from typing import Any

import structlog

MASK = "***"
_SECRET_KEY = re.compile(
    r"(_key|_token|_secret|_password)$|^(token|password|secret|authorization)$", re.IGNORECASE
)
_secret_values: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """Запомнить значения секретов: они будут заменены на *** в любом тексте логов."""
    _secret_values.update(v for v in values if v)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        for secret in sorted(_secret_values, key=len, reverse=True):
            value = value.replace(secret, MASK)
        return value
    if isinstance(value, dict):
        return mask_secrets(value)
    if isinstance(value, list | tuple):
        return type(value)(_mask_value(v) for v in value)
    return value


def mask_secrets(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Заменить секреты на ***: по имени поля (*_KEY, *_TOKEN…) и по известным значениям."""
    result: dict[str, Any] = {}
    for key, value in event_dict.items():
        if _SECRET_KEY.search(str(key)) and value not in (None, ""):
            result[key] = MASK
        else:
            result[key] = _mask_value(value)
    return result


def _mask_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return mask_secrets(dict(event_dict))


def setup_logging(level: str = "INFO") -> None:
    """JSON в stdout: timestamp, level, event, request_id (из contextvars), поля вызова."""
    numeric = std_logging.getLevelNamesMapping().get(level.upper(), std_logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.format_exc_info,
            _mask_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def bind_request_id(request_id: str | None = None) -> str:
    """Привязать request_id к текущему контексту (генерирует, если не задан)."""
    rid = request_id or uuid.uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=rid)
    return rid
