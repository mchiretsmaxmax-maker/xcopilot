"""Сигнал public_metrics: публичные цифры поста по ссылке (feat/auto-outcome, ADR 0033).

Цифры из fxtwitter (лайки, ретвиты, ответы, просмотры, подписчики автора) дают автоисход.
Он слабее кнопки владельца: в v_material_best_signal public_metrics стоит после owner_label,
поэтому ручная оценка перекрывает автоматическую, а точные цифры (X API, скрин, ввод) — обе.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "origin IN ('x_api', 'screenshot', 'manual_numbers', 'owner_label', 'owner_pick', 'own_sync')"
NEW = (
    "origin IN ('x_api', 'screenshot', 'manual_numbers', 'owner_label', 'owner_pick', 'own_sync',"
    " 'public_metrics')"
)
OLD_COMMENT = (
    "Источник: x_api, screenshot, manual_numbers, owner_label (кнопка), owner_pick"
    " (сам факт отбора), own_sync (свой пост из этапа 14)"
)
NEW_COMMENT = OLD_COMMENT + ", public_metrics (публичные цифры по ссылке, автоисход)"

VIEW = """
CREATE OR REPLACE VIEW v_material_best_signal AS
SELECT DISTINCT ON (m.source_id) m.*
FROM material_signals m
ORDER BY m.source_id,
         CASE m.origin WHEN 'own_sync' THEN 1 WHEN 'x_api' THEN 2 WHEN 'screenshot' THEN 3
                       WHEN 'manual_numbers' THEN 4 WHEN 'owner_label' THEN 5
                       WHEN 'public_metrics' THEN 6 ELSE 7 END,
         m.observed_at DESC
"""

OLD_VIEW = """
CREATE OR REPLACE VIEW v_material_best_signal AS
SELECT DISTINCT ON (m.source_id) m.*
FROM material_signals m
ORDER BY m.source_id,
         CASE m.origin WHEN 'own_sync' THEN 1 WHEN 'x_api' THEN 2 WHEN 'screenshot' THEN 3
                       WHEN 'manual_numbers' THEN 4 WHEN 'owner_label' THEN 5 ELSE 6 END,
         m.observed_at DESC
"""


VIEW_COMMENT = (
    "COMMENT ON VIEW v_material_best_signal IS"
    " 'Одно наблюдение на материал, по которому считается исход'"
)


def _origin(check: str, comment: str) -> None:
    op.drop_constraint(
        op.f("ck_material_signals_origin_allowed"), "material_signals", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_material_signals_origin_allowed"), "material_signals", sa.text(check)
    )
    op.alter_column(
        "material_signals",
        "origin",
        existing_type=sa.String(),
        existing_nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    _origin(NEW, NEW_COMMENT)
    op.execute(VIEW)
    op.execute(VIEW_COMMENT)


def downgrade() -> None:
    op.execute("DELETE FROM material_signals WHERE origin = 'public_metrics'")
    op.execute(OLD_VIEW)
    op.execute(VIEW_COMMENT)
    _origin(OLD, OLD_COMMENT)
