"""Отметки эксплуатации: последний бэкап и последний алерт по ключу (ТЗ, этап 15; ADR 0028).

`ops_marks` — одна строка на (аккаунт, ключ). `backup.ok` / `backup.failed` пишет
`scripts/backup.sh` у системного аккаунта global; `alert:<код>[:<объект>]` пишет `ops.alerts`
у аккаунта, чтобы один и тот же алерт не повторялся чаще раза в час.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_marks",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            nullable=False,
            comment="Аккаунт отметки; общие — системный global",
        ),
        sa.Column(
            "key",
            sa.String(),
            nullable=False,
            comment="Ключ: backup.ok, backup.failed, alert:<код>[:<объект>]",
        ),
        sa.Column(
            "at", sa.DateTime(timezone=True), nullable=False, comment="Когда случилось (UTC)"
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Подробности: файл и размер бэкапа, текст алерта",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_ops_marks_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_marks")),
        sa.UniqueConstraint("account_id", "key", name=op.f("uq_ops_marks_account_id_key")),
        comment="Отметки эксплуатации (этап 15): последний бэкап, последний алерт по ключу."
        " Глобальные отметки — у системного аккаунта global",
    )


def downgrade() -> None:
    op.drop_table("ops_marks")
