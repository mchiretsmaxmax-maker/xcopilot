"""Ссылка на сущность в журнале вызовов моделей (стоимость по черновику, этап 4).

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column(
            "entity_type",
            sa.String(),
            nullable=True,
            comment="Тип сущности, ради которой вызвали, например draft",
        ),
    )
    op.add_column(
        "llm_calls",
        sa.Column("entity_id", sa.Uuid(), nullable=True, comment="ID сущности"),
    )
    op.create_index(
        "ix_llm_calls_account_id_entity",
        "llm_calls",
        ["account_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_account_id_entity", table_name="llm_calls")
    op.drop_column("llm_calls", "entity_id")
    op.drop_column("llm_calls", "entity_type")
