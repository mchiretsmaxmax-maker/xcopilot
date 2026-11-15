"""Априоры знания в колонках `knowledge_items` (ТЗ v3.1, этап 9A, задача 9).

Копия общего шаблона из аккаунта global приходит в аккаунт «с априором, ослабленным вдвое»
(раздел 8.3). Раньше `knowledge_recompute_scores` держала априоры 1/1 и 2/1 в теле функции и
затирала alpha/beta при каждом пересчёте; теперь функция берёт априор из колонок знания.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIORS = (
    ("eff_prior_alpha", "1", "Априор подтверждений результативности"),
    ("eff_prior_beta", "1", "Априор опровержений результативности"),
    ("fit_prior_alpha", "2", "Априор принятий совместимости"),
    ("fit_prior_beta", "1", "Априор отказов совместимости"),
)
CK = "ck_knowledge_items_priors_positive"
FN_SIG = "knowledge_recompute_scores(uuid, uuid, timestamptz, numeric)"
FN_COMMENT = (
    f"COMMENT ON FUNCTION {FN_SIG} IS 'Пересчитывает веса знаний аккаунта из неотозванных"
    " доказательств со спадом по свежести; априор — из колонок знания. p_now в прошлом даёт"
    " состояние на ту дату'"
)


def _m0004() -> ModuleType:
    path = Path(__file__).with_name("0004_brain_db.py")
    spec = importlib.util.spec_from_file_location("_xc_m0004", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_fn() -> str:
    fn: str = _m0004().RECOMPUTE_FN
    return fn.split("COMMENT ON FUNCTION")[0].strip().rstrip(";")


def _new_fn() -> str:
    fn = _old_fn().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    tail = "coalesce(sum(ev.w) FILTER (WHERE ev.dimension = '{}' AND ev.polarity = {})"
    swaps = {
        f"{base} + {tail.format(dim, pol)}": f"i.{col} + {tail.format(dim, pol)}"
        for base, dim, pol, col in (
            ("1", "eff", " 1", "eff_prior_alpha"),
            ("1", "eff", "-1", "eff_prior_beta"),
            ("2", "fit", " 1", "fit_prior_alpha"),
            ("1", "fit", "-1", "fit_prior_beta"),
        )
    }
    for old, new in swaps.items():
        if old not in fn:
            raise RuntimeError(f"0007: не найдено в функции 0004: {old}")
        fn = fn.replace(old, new)
    return fn


def upgrade() -> None:
    for name, default, comment in PRIORS:
        op.add_column(
            "knowledge_items",
            sa.Column(
                name,
                sa.Numeric(),
                server_default=sa.text(default),
                nullable=False,
                comment=f"{comment} ({default}; у копии общего шаблона — вдвое меньше)",
            ),
        )
    op.create_check_constraint(
        CK,
        "knowledge_items",
        sa.text(
            "eff_prior_alpha > 0 AND eff_prior_beta > 0 AND fit_prior_alpha > 0"
            " AND fit_prior_beta > 0"
        ),
    )
    op.execute(_new_fn())
    op.execute(FN_COMMENT)


def downgrade() -> None:
    op.execute(_old_fn().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.drop_constraint(CK, "knowledge_items", type_="check")
    for name, _, _ in reversed(PRIORS):
        op.drop_column("knowledge_items", name)
