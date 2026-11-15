"""Рендер пакета знаний: один текст для любой модели, без цифр уверенности (ТЗ v3.1, 4.3)."""

from __future__ import annotations

from xcopilot.brain.schemas import KnowledgePack


def to_markdown(pack: KnowledgePack) -> str:
    raise NotImplementedError


def to_json(pack: KnowledgePack) -> str:
    raise NotImplementedError
