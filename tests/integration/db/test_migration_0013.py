"""Миграция 0013 `reply_harvest` (ADR 0034, «Миграция 0013»; этап R2 самообучения на ответах)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command

from tests.dbutil import fetch
from xcopilot.db.migrate import alembic_config

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def columns(url: str, table: str) -> set[str]:
    rows = fetch(
        url,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return {r["column_name"] for r in rows}


def seed(url: str, *, harvest: bool) -> dict[str, Any]:
    """Аккаунт, 2 автора, 3 материала, знание с доказательствами (и материал сбора на 0013)."""
    [acc] = fetch(url, "INSERT INTO accounts (handle, tz) VALUES ('m13', 'UTC') RETURNING id")
    a = acc["id"]
    authors = [
        fetch(url, "INSERT INTO authors (account_id, handle) VALUES ($1, $2) RETURNING id", a, h)[
            0
        ]["id"]
        for h in ("w1", "w2")
    ]
    [it] = fetch(
        url,
        "INSERT INTO knowledge_items (account_id, kind, layer, element, statement, guidance,"
        " target_metric, risk_class, status, origin) VALUES ($1, 'thesis', 'craft', 'hook',"
        " 'Цифра в первой строке', 'Начни с цифры', 'engagement', 'B', 'candidate', 'materials')"
        " RETURNING id",
        a,
    )
    out: dict[str, Any] = {"account": a, "item": it["id"], "sources": []}
    for n, (auth, w_author) in enumerate(((authors[0], 1), (authors[0], 0.5), (authors[1], 1))):
        [src] = fetch(
            url,
            "INSERT INTO sources (account_id, kind, stance, origin, content_hash, author_id)"
            " VALUES ($1, 'post', 'inspiration', 'screenshot', $2, $3) RETURNING id",
            a,
            f"h{n}",
            auth,
        )
        out["sources"].append(src["id"])
        fetch(
            url,
            "INSERT INTO knowledge_evidence (account_id, item_id, source_id, dimension, basis,"
            " polarity, author_id, w_source, w_scope, w_magnitude, w_author, observed_at,"
            " created_at) VALUES ($1, $2, $3, 'eff', 'observed_outcome', $4, $5, 0.7, 1.0, 1.0,"
            " $6, $7, $7)",
            a,
            it["id"],
            src["id"],
            -1 if n == 1 else 1,
            auth,
            w_author,
            NOW,
        )
    if harvest:
        [h] = fetch(
            url,
            "INSERT INTO sources (account_id, kind, stance, origin, content_hash, author_id, meta)"
            " VALUES ($1, 'reply', 'inspiration', 'reply_harvest', 'hr', $2,"
            ' \'{"harvest": "42"}\'::jsonb) RETURNING id',
            a,
            authors[1],
        )
        out["harvest"] = h["id"]
        fetch(
            url,
            "INSERT INTO material_outcomes (account_id, source_id, method_version, basis,"
            " signal_origin, reliability, is_current)"
            " VALUES ($1, $2, 1, 'sibling_rank', 'public_metrics', 0.3, true)",
            a,
            h["id"],
        )
        fetch(
            url,
            "INSERT INTO knowledge_evidence (account_id, item_id, source_id, dimension, basis,"
            " polarity, author_id, w_source, w_scope, w_magnitude, w_author, observed_at,"
            " created_at, context_key) VALUES ($1, $2, $3, 'eff', 'observed_outcome', 1, $4,"
            " 0.3, 0.8, 1.0, 1, $5, $5, '42')",
            a,
            it["id"],
            h["id"],
            authors[1],
            NOW,
        )
    return out


SCORES = (
    "SELECT eff_alpha, eff_beta, fit_alpha, fit_beta, eff_conf, fit_conf, quality, evidence_n,"
    " independent_authors, own_support, own_contra, occurrences FROM knowledge_items"
    " WHERE id = $1"
)


def recompute(url: str, account: Any) -> None:
    fetch(url, "SELECT knowledge_recompute_scores($1, NULL, $2)", account, NOW)


# 1
def test_0013_upgrade_downgrade(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    # чистая база: вверх, вниз, снова вверх
    command.upgrade(cfg, "0013")
    assert "context_key" in columns(empty_db_url, "knowledge_evidence")
    assert "independent_contexts" in columns(empty_db_url, "knowledge_items")
    command.downgrade(cfg, "0012")
    assert "context_key" not in columns(empty_db_url, "knowledge_evidence")
    assert "independent_contexts" not in columns(empty_db_url, "knowledge_items")
    command.upgrade(cfg, "0013")
    # заполненная база
    data = seed(empty_db_url, harvest=True)
    recompute(empty_db_url, data["account"])
    command.downgrade(cfg, "0012")
    assert fetch(empty_db_url, "SELECT 1 FROM sources WHERE origin = 'reply_harvest'") == []
    assert fetch(empty_db_url, "SELECT 1 FROM material_outcomes WHERE basis = 'sibling_rank'") == []
    [h] = fetch(empty_db_url, "SELECT removed_at FROM sources WHERE id = $1", data["harvest"])
    assert h["removed_at"] is not None
    live = fetch(
        empty_db_url,
        "SELECT 1 FROM knowledge_evidence WHERE source_id = $1 AND revoked_at IS NULL",
        data["harvest"],
    )
    assert live == []
    with pytest.raises(Exception, match="check"):
        fetch(
            empty_db_url,
            "INSERT INTO sources (account_id, kind, stance, origin, content_hash)"
            " VALUES ($1, 'reply', 'inspiration', 'reply_harvest', 'again')",
            data["account"],
        )
    recompute(empty_db_url, data["account"])  # функция 0012 работает после отката
    command.upgrade(cfg, "head")


# 2
def test_origin_reply_harvest_allowed(db_url: str) -> None:
    [acc] = fetch(db_url, "INSERT INTO accounts (handle, tz) VALUES ('o13', 'UTC') RETURNING id")
    sql = (
        "INSERT INTO sources (account_id, kind, stance, origin, content_hash)"
        " VALUES ($1, 'reply', 'inspiration', $2, $3)"
    )
    fetch(db_url, sql, acc["id"], "reply_harvest", "ok")
    with pytest.raises(Exception, match="ck_sources_origin_allowed"):
        fetch(db_url, sql, acc["id"], "reply_scrape", "bad")


# 3
def test_basis_sibling_rank_allowed(db_url: str) -> None:
    [acc] = fetch(db_url, "INSERT INTO accounts (handle, tz) VALUES ('b13', 'UTC') RETURNING id")
    [src] = fetch(
        db_url,
        "INSERT INTO sources (account_id, kind, stance, origin, content_hash)"
        " VALUES ($1, 'reply', 'inspiration', 'reply_harvest', 'b') RETURNING id",
        acc["id"],
    )
    fetch(
        db_url,
        "INSERT INTO material_outcomes (account_id, source_id, method_version, basis,"
        " signal_origin, reliability, is_current, tier_engagement)"
        " VALUES ($1, $2, 1, 'sibling_rank', 'public_metrics', 0.3, true, 'hit')",
        acc["id"],
        src["id"],
    )
    [row] = fetch(db_url, "SELECT basis FROM material_outcomes WHERE source_id = $1", src["id"])
    assert row["basis"] == "sibling_rank"


# 14
def test_old_evidence_unchanged(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "0012")
    data = seed(empty_db_url, harvest=False)
    recompute(empty_db_url, data["account"])
    weights = "SELECT id, weight, w_author FROM knowledge_evidence ORDER BY id"
    before_w = [dict(r) for r in fetch(empty_db_url, weights)]
    before = dict(fetch(empty_db_url, SCORES, data["item"])[0])
    command.upgrade(cfg, "0013")
    assert [
        r["context_key"] for r in fetch(empty_db_url, "SELECT context_key FROM knowledge_evidence")
    ] == [None] * 3
    recompute(empty_db_url, data["account"])
    assert [dict(r) for r in fetch(empty_db_url, weights)] == before_w
    assert dict(fetch(empty_db_url, SCORES, data["item"])[0]) == before
    [k] = fetch(
        empty_db_url, "SELECT independent_contexts FROM knowledge_items WHERE id = $1", data["item"]
    )
    assert k["independent_contexts"] == 2  # 2 подтверждения из 2 разных материалов
