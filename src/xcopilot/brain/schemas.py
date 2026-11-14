"""Схемы «Мозга» (ТЗ v3.1, приложение D): пока — чтение скриншота (этап 7).

Модель обязана вернуть JSON по этим схемам. Лишние поля запрещены, длины ограничены:
это часть защиты от мусора и инъекций, а не оформление. Разбор материала, извлечение
знаний и пакет знаний добавят этапы 7A и 9B.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 1. Скриншот (роль cheap, vision)
class Counter(Strict):
    value: int | None = Field(None, ge=0, description="Число целиком: 12400, а не 12.4K")
    as_shown: str | None = Field(None, max_length=16, description="Как написано на скриншоте")
    confidence: float = Field(ge=0, le=1)


class ScreenshotRead(Strict):
    is_x_post: bool
    kind: Literal["post", "reply", "quote", "thread_part", "profile", "other"]
    author_handle: str | None = Field(None, max_length=32)
    author_followers: Counter | None = None
    text: str = Field(max_length=6000, description="Текст поста дословно, с переносами строк")
    parent_author_handle: str | None = Field(None, max_length=32)
    parent_text: str | None = Field(None, max_length=3000)
    posted_at_shown: str | None = Field(None, max_length=40)
    views: Counter | None = None
    likes: Counter | None = None
    replies: Counter | None = None
    reposts: Counter | None = None
    bookmarks: Counter | None = None
    has_media: bool = False
    unreadable: list[str] = Field(default_factory=list, max_length=5)
