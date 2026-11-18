"""Сбор чужих ответов (ADR 0034): origin reply_harvest, basis sibling_rank, контекст доказательства.

Заглушка этапа R2 (red): тело — следующим коммитом.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18
"""

from collections.abc import Sequence

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
