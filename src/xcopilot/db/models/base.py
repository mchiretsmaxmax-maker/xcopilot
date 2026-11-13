"""Базовый класс моделей."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """База всех моделей."""
