"""Генерация docs/db/schema.md и docs/db/er.md из метаданных моделей."""

from __future__ import annotations

from sqlalchemy import MetaData


def render_schema_md(metadata: MetaData) -> str:
    """Markdown: таблица -> назначение -> колонки с описанием."""
    raise NotImplementedError


def render_er_mermaid(metadata: MetaData) -> str:
    """Markdown с ER-диаграммой Mermaid."""
    raise NotImplementedError
