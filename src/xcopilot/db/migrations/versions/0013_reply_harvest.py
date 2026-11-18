"""Сбор чужих ответов (ADR 0034, «Миграция 0013»): origin, basis, контекст доказательства.

1. `sources.origin` + `reply_harvest` (ответы и цитаты, собранные под постом режима ответов).
2. `material_outcomes.basis` + `sibling_rank` (исход по месту среди соседних ответов).
3. `knowledge_evidence.context_key` — контекст независимости: ответы под одним постом считаются
   одним контекстом, поэтому один вирусный пост не «доказывает» приём (ADR 0034, п. 6).
4. `knowledge_items.independent_contexts` — число разных контекстов среди подтверждений.
5. `knowledge_recompute_scores` считает контексты: точечная правка текста функции из 0007.
6. `v_knowledge_cards` (список колонок с `independent_authors`) — плюс `independent_contexts`.

Старые доказательства получают `context_key = NULL` (= свой контекст): их веса не меняются.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINS_OLD = (
    "'manual_text', 'link_oembed', 'x_api', 'screenshot', 'tg_forward', 'file', 'url_fetch',"
    " 'history_backfill', 'radar'"
)
ORIGINS_NEW = ORIGINS_OLD + ", 'reply_harvest'"
ORIGIN_COMMENT_OLD = (
    "Канал получения: manual_text, link_oembed, x_api, screenshot, tg_forward,"
    " file, url_fetch, history_backfill, radar"
)
ORIGIN_COMMENT_NEW = ORIGIN_COMMENT_OLD + ", reply_harvest (сбор ответов под постом)"

BASES_OLD = "'own_baseline', 'rates', 'owner_label', 'owner_pick', 'none'"
BASES_NEW = BASES_OLD + ", 'sibling_rank'"
BASIS_COMMENT_OLD = (
    "На чём основан исход: own_baseline — перцентиль среди своих постов; rates —"
    " доли от просмотров; owner_label — кнопка; owner_pick — только факт отбора; none —"
    " данных нет"
)
BASIS_COMMENT_NEW = BASIS_COMMENT_OLD + "; sibling_rank — место среди ответов под тем же постом"

CONTEXT_COMMENT = (
    "Контекст независимости: id исходного поста для ответов сбора, иначе NULL (= свой контекст)"
)
CONTEXTS_COMMENT = (
    "Число разных контекстов среди подтверждений: ответы под одним постом — один"
    " контекст, прочий материал — свой"
)
W_AUTHOR_COMMENT_OLD = "Скидка за повтор автора: 1/k для k-го доказательства от того же автора"
W_AUTHOR_COMMENT_NEW = (
    "Скидка за зависимость: min(1/k по автору, 1/k по контексту) для k-го доказательства"
)

FN_SIG = "knowledge_recompute_scores(uuid, uuid, timestamptz, numeric)"
FN_COMMENT = (
    f"COMMENT ON FUNCTION {FN_SIG} IS 'Пересчитывает веса знаний аккаунта из неотозванных"
    " доказательств со спадом по свежести; априор — из колонок знания; независимые контексты —"
    " по context_key (NULL = свой материал). p_now в прошлом даёт состояние на ту дату'"
)
CARDS_COMMENT = (
    "COMMENT ON VIEW v_knowledge_cards IS 'Знание с областью действия, словесной уверенностью"
    " и числом открытых противоречий'"
)

# Откат: материалы сбора убираются, их доказательства отзываются (как `evidence.revoke`
# с причиной source_removed), а канал становится manual_text с пометкой в meta — иначе старый
# CHECK не встанет. Веса затронутых знаний пересчитываются.
DOWN_HARVEST = (
    "UPDATE knowledge_evidence e SET revoked_at = now(), revoked_reason = 'source_removed'"
    " FROM sources s WHERE s.id = e.source_id AND s.origin = 'reply_harvest'"
    " AND e.revoked_at IS NULL",
    "UPDATE sources SET removed_at = coalesce(removed_at, now()), origin = 'manual_text',"
    ' meta = meta || \'{"origin_before_0013_downgrade": "reply_harvest"}\'::jsonb'
    " WHERE origin = 'reply_harvest'",
    "DELETE FROM material_outcomes WHERE basis = 'sibling_rank'",
)
RECOMPUTE_ALL = (
    "SELECT knowledge_recompute_scores(a.id) FROM accounts a"
    " WHERE EXISTS (SELECT 1 FROM knowledge_items k WHERE k.account_id = a.id)"
)


def _module(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_xc_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fn_0007() -> str:
    fn: str = _module("0007_knowledge_priors.py")._new_fn()
    return fn


def _fn_0013() -> str:
    fn = _fn_0007()
    swaps = {
        "SELECT e.item_id, e.dimension, e.polarity, e.basis, e.author_id, e.observed_at,": (
            "SELECT e.item_id, e.dimension, e.polarity, e.basis, e.author_id, e.observed_at,"
            "\n           e.source_id, e.context_key,"
        ),
        "AS n_authors,": (
            "AS n_authors,\n           count(DISTINCT coalesce(ev.context_key, ev.source_id::text))"
            " FILTER (WHERE ev.dimension = 'eff' AND ev.polarity = 1) AS n_contexts,"
        ),
        "independent_authors = f.n_authors,": (
            "independent_authors = f.n_authors, independent_contexts = f.n_contexts,"
        ),
    }
    for old, new in swaps.items():
        if fn.count(old) != 1:
            raise RuntimeError(f"0013: не найдено ровно одно вхождение в функции 0007: {old}")
        fn = fn.replace(old, new)
    return fn


def _cards_0004() -> str:
    views: str = _module("0004_brain_db.py").VIEWS
    start = views.index("CREATE VIEW v_knowledge_cards")
    end = views.index("COMMENT ON VIEW v_knowledge_cards")
    return views[start:end].strip().rstrip(";")


def _cards_0013() -> str:
    old = "k.evidence_n, k.independent_authors, k.own_support,"
    view = _cards_0004()
    if view.count(old) != 1:
        raise RuntimeError(f"0013: не найдено в v_knowledge_cards: {old}")
    return view.replace(
        old, "k.evidence_n, k.independent_authors, k.independent_contexts, k.own_support,"
    )


def _check(table: str, name: str, column: str, values: str, comment: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, sa.text(f"{column} IN ({values})"))
    op.alter_column(
        table, column, existing_type=sa.String(), existing_nullable=False, comment=comment
    )


def _w_author(comment: str) -> None:
    op.alter_column(
        "knowledge_evidence",
        "w_author",
        existing_type=sa.Numeric(),
        existing_nullable=False,
        existing_server_default=sa.text("1"),
        comment=comment,
    )


def upgrade() -> None:
    _check("sources", "ck_sources_origin_allowed", "origin", ORIGINS_NEW, ORIGIN_COMMENT_NEW)
    _check(
        "material_outcomes",
        "ck_material_outcomes_basis_allowed",
        "basis",
        BASES_NEW,
        BASIS_COMMENT_NEW,
    )
    op.add_column(
        "knowledge_evidence",
        sa.Column("context_key", sa.Text(), nullable=True, comment=CONTEXT_COMMENT),
    )
    op.create_index("ix_evidence_context", "knowledge_evidence", ["item_id", "context_key"])
    _w_author(W_AUTHOR_COMMENT_NEW)
    op.add_column(
        "knowledge_items",
        sa.Column(
            "independent_contexts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment=CONTEXTS_COMMENT,
        ),
    )
    op.execute(_fn_0013())
    op.execute(FN_COMMENT)
    op.execute("DROP VIEW v_knowledge_cards")
    op.execute(_cards_0013())
    op.execute(CARDS_COMMENT)
    # Заполнить independent_contexts у существующих знаний (остальные веса не меняются).
    op.execute(
        "UPDATE knowledge_items k SET independent_contexts = c.n FROM ("
        " SELECT e.item_id, count(DISTINCT coalesce(e.context_key, e.source_id::text)) AS n"
        " FROM knowledge_evidence e WHERE e.dimension = 'eff' AND e.polarity = 1"
        " AND e.revoked_at IS NULL GROUP BY e.item_id) c WHERE c.item_id = k.id"
    )


def downgrade() -> None:
    for sql in DOWN_HARVEST:
        op.execute(sql)
    op.execute("DROP VIEW v_knowledge_cards")
    op.execute(_cards_0004())
    op.execute(CARDS_COMMENT)
    op.execute(_fn_0007())
    op.execute(_module("0007_knowledge_priors.py").FN_COMMENT)
    op.execute(RECOMPUTE_ALL)
    op.drop_column("knowledge_items", "independent_contexts")
    _w_author(W_AUTHOR_COMMENT_OLD)
    op.drop_index("ix_evidence_context", table_name="knowledge_evidence")
    op.drop_column("knowledge_evidence", "context_key")
    _check(
        "material_outcomes",
        "ck_material_outcomes_basis_allowed",
        "basis",
        BASES_OLD,
        BASIS_COMMENT_OLD,
    )
    _check("sources", "ck_sources_origin_allowed", "origin", ORIGINS_OLD, ORIGIN_COMMENT_OLD)
