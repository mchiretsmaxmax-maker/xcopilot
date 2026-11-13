"""Шифрование секретов (Fernet) с версией ключа для ротации.

SECRETS_KEY: один ключ Fernet (версия 1) или список «версия:ключ» через запятую,
например «1:старый,2:новый». Шифруем старшей версией, расшифровываем любой из известных.
Текст исключений никогда не содержит ни ключей, ни открытого текста.
"""

from __future__ import annotations

from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from xcopilot.errors import AppError


class DecryptError(AppError):
    """Не удалось расшифровать: неверный ключ или повреждённые данные."""


class SecretBox:
    """Ключи по версиям; шифрует текущей (старшей) версией."""

    def __init__(self, keys: Mapping[int, str]) -> None:
        if not keys:
            raise ValueError("SecretBox: нужен хотя бы один ключ")
        try:
            fernets = {v: Fernet(k.encode()) for v, k in keys.items()}
        except (ValueError, TypeError) as exc:
            raise ValueError("SecretBox: ключ не похож на ключ Fernet") from exc
        self._version = max(fernets)
        ordered = [fernets[v] for v in sorted(fernets, reverse=True)]
        self._multi = MultiFernet(ordered)

    @classmethod
    def from_string(cls, value: str) -> SecretBox:
        """SECRETS_KEY: «ключ» (версия 1) или «1:ключ1,2:ключ2»."""
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts:
            raise ValueError("SECRETS_KEY пуст")
        keys: dict[int, str] = {}
        for part in parts:
            version, sep, key = part.partition(":")
            if sep and version.isdigit():
                keys[int(version)] = key
            elif len(parts) == 1:
                keys[1] = part
            else:
                raise ValueError("SECRETS_KEY: ожидается «версия:ключ» через запятую")
        return cls(keys)

    @property
    def key_version(self) -> int:
        """Версия ключа, которой шифруются новые секреты."""
        return self._version

    def encrypt(self, plain: str) -> bytes:
        return self._multi.encrypt(plain.encode())

    def decrypt(self, blob: bytes) -> str:
        try:
            return self._multi.decrypt(blob).decode()
        except (InvalidToken, TypeError, ValueError):
            raise DecryptError("секрет не расшифрован: неверный ключ или данные") from None
