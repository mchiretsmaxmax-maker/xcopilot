"""Шифрование секретов (Fernet) с версией ключа для ротации."""

from __future__ import annotations

from collections.abc import Mapping

from xcopilot.errors import AppError


class DecryptError(AppError):
    """Не удалось расшифровать: неверный ключ или повреждённые данные."""


class SecretBox:
    """Ключи по версиям; шифрует текущей (старшей) версией."""

    def __init__(self, keys: Mapping[int, str]) -> None:
        raise NotImplementedError

    @classmethod
    def from_string(cls, value: str) -> SecretBox:
        """SECRETS_KEY: «ключ» (версия 1) или «1:ключ1,2:ключ2»."""
        raise NotImplementedError

    @property
    def key_version(self) -> int:
        raise NotImplementedError

    def encrypt(self, plain: str) -> bytes:
        raise NotImplementedError

    def decrypt(self, blob: bytes) -> str:
        raise NotImplementedError
