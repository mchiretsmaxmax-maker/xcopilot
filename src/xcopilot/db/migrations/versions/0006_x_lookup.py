"""Режим X `lookup` и бюджет `x_lookup` (ТЗ v3.1, этап 6): расширение двух CHECK и колонка
`x_api_calls.budget_scope` (лимиты x_api и x_lookup считаются раздельно).

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

MODES_OLD = "'off', 'owned', 'paid'"
MODES_NEW = "'off', 'owned', 'lookup', 'paid'"
SCOPES_OLD = "'llm', 'x_api', 'sniper'"
SCOPES_NEW = "'llm', 'x_api', 'x_lookup', 'sniper'"


def _swap(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, sa.text(condition))


def upgrade() -> None:
    op.alter_column(
        "accounts",
        "x_read_mode",
        existing_type=sa.String(),
        existing_nullable=False,
        existing_server_default=sa.text("'owned'"),
        comment="Режим чтения X: off / owned / lookup / paid",
    )
    op.alter_column(
        "budgets",
        "scope",
        existing_type=sa.String(),
        existing_nullable=False,
        comment="На что: llm / x_api / x_lookup / sniper",
    )
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
    _swap("accounts", "ck_accounts_x_read_mode_allowed", f"x_read_mode IN ({MODES_NEW})")
    _swap("budgets", "ck_budgets_scope_allowed", f"scope IN ({SCOPES_NEW})")


def downgrade() -> None:
    op.alter_column(
        "accounts",
        "x_read_mode",
        existing_type=sa.String(),
        existing_nullable=False,
        existing_server_default=sa.text("'owned'"),
        comment="Режим чтения X: off / owned / paid",
    )
    op.alter_column(
        "budgets",
        "scope",
        existing_type=sa.String(),
        existing_nullable=False,
        comment="На что: llm / x_api / sniper",
    )
    op.drop_constraint(op.f("ck_x_api_calls_budget_scope_allowed"), "x_api_calls", type_="check")
    op.drop_column("x_api_calls", "budget_scope")
    op.execute("UPDATE accounts SET x_read_mode = 'owned' WHERE x_read_mode = 'lookup'")
    op.execute("DELETE FROM budgets WHERE scope = 'x_lookup'")
    _swap("accounts", "ck_accounts_x_read_mode_allowed", f"x_read_mode IN ({MODES_OLD})")
    _swap("budgets", "ck_budgets_scope_allowed", f"scope IN ({SCOPES_OLD})")
