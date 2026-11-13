"""Роли моделей."""

from enum import StrEnum


class Role(StrEnum):
    WRITER = "writer"
    JUDGE = "judge"
    CHEAP = "cheap"
