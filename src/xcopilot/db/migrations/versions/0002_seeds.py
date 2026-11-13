"""Сиды: системный аккаунт global, модель эмбеддингов, лимиты по умолчанию, расписание партиций.

Лимиты совпадают с умолчаниями Settings: LLM $5 в день, X API $1 в день (в микродолларах).

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
GLOBAL = "(SELECT id FROM accounts WHERE kind = 'global' AND handle = 'global')"


def upgrade() -> None:
    op.execute(
        "INSERT INTO accounts (kind, handle, x_read_mode, niche)"
        " VALUES ('global', 'global', 'off', 'системный аккаунт: глобальные правила и задачи')"
    )
    op.execute(
        f"INSERT INTO embedding_models (name, dim, active) VALUES ('{EMBEDDING_MODEL}', 384, true)"
    )
    op.execute(
        "INSERT INTO budgets (account_id, scope, period, limit_micro_usd, hard) VALUES"
        f" ({GLOBAL}, 'llm', 'day', 5000000, true),"
        f" ({GLOBAL}, 'x_api', 'day', 1000000, true)"
    )
    op.execute(
        "INSERT INTO schedules (account_id, kind, at_local_time, weekday, enabled)"
        f" VALUES ({GLOBAL}, 'db.ensure_partitions', '03:00', NULL, true)"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM schedules WHERE account_id = {GLOBAL}")
    op.execute(f"DELETE FROM budgets WHERE account_id = {GLOBAL}")
    op.execute(f"DELETE FROM embedding_models WHERE name = '{EMBEDDING_MODEL}'")
    op.execute("DELETE FROM accounts WHERE kind = 'global' AND handle = 'global'")
