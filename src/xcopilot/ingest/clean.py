"""Чистка текста: пробелы, невидимые символы, служебные параметры ссылок."""

from __future__ import annotations


def strip_tracking(url: str) -> str:
    raise NotImplementedError


def normalize(text: str) -> str:
    raise NotImplementedError
