"""Журнал X: колонка `x_api_calls.budget_scope` (ТЗ v3.1, этап 6).

Лимиты `x_api` и `x_lookup` считаются раздельно, поэтому строка журнала помнит, из какого
лимита расход. CHECK режима `lookup` и scope `x_lookup` добавила миграция 0004.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "x_api_calls",
        sa.Column(
            "budget_scope",
            sa.String(),
            server_default=sa.text("'x_api'"),
            nullable=False,
            comment="Из какого лимита расход: x_api (общий) или x_lookup (точечное чтение)",
        ),
    )
    op.create_check_constraint(
        op.f("ck_x_api_calls_budget_scope_allowed"),
        "x_api_calls",
        sa.text("budget_scope IN ('x_api', 'x_lookup')"),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_x_api_calls_budget_scope_allowed"), "x_api_calls", type_="check")
    op.drop_column("x_api_calls", "budget_scope")
