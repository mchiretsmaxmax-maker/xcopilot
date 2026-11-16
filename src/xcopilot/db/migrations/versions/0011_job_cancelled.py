"""Отмена задач: статус jobs.status = 'cancelled' (fix/bot-control).

Владелец отменяет задачу кнопкой «❌ Отменить»: queued → cancelled сразу, running →
cancelled (воркер прерывает обработчик, результат выбрасывается, complete статус не меняет).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('queued', 'running', 'done', 'dead')"
NEW = "status IN ('queued', 'running', 'done', 'dead', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_status_allowed"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status_allowed"), "jobs", sa.text(NEW))
    op.alter_column(
        "jobs",
        "status",
        existing_type=sa.String(),
        existing_nullable=False,
        existing_server_default=sa.text("'queued'"),
        comment="queued / running / done / dead / cancelled",
    )


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'dead' WHERE status = 'cancelled'")
    op.drop_constraint(op.f("ck_jobs_status_allowed"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status_allowed"), "jobs", sa.text(OLD))
    op.alter_column(
        "jobs",
        "status",
        existing_type=sa.String(),
        existing_nullable=False,
        existing_server_default=sa.text("'queued'"),
        comment="queued / running / done / dead",
    )
