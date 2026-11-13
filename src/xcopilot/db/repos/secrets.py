"""Зашифрованные секреты аккаунта."""

from __future__ import annotations

from xcopilot.db.models import Secret
from xcopilot.db.repos.base import TenantRepo
from xcopilot.security.crypto import SecretBox


class SecretsRepo(TenantRepo[Secret]):
    model = Secret

    async def get_by_kind(self, kind: str) -> Secret | None:
        """Строка секрета своего аккаунта по kind."""
        stmt = self.scoped().where(Secret.kind == kind)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_kind_for_update(self, kind: str) -> Secret | None:
        """Как get_by_kind, но с блокировкой строки до конца транзакции (SELECT … FOR UPDATE)."""
        stmt = (
            self.scoped()
            .where(Secret.kind == kind)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def put(self, kind: str, plain: str, box: SecretBox) -> Secret:
        """Зашифровать и сохранить секрет; существующий обновить."""
        ciphertext = box.encrypt(plain)
        row = await self.get_by_kind(kind)
        if row is None:
            return await self.add(kind=kind, ciphertext=ciphertext, key_version=box.key_version)
        return await self.update(row.id, ciphertext=ciphertext, key_version=box.key_version)

    async def get_plain(self, kind: str, box: SecretBox) -> str | None:
        """Расшифровать секрет своего аккаунта по kind."""
        row = await self.get_by_kind(kind)
        if row is None:
            return None
        return box.decrypt(row.ciphertext)
