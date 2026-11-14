"""Миграция 0004 «Мозг»: перенос sources.kind/origin вверх и обратно, lessons возвращается."""

from alembic import command

from tests.dbutil import fetch
from xcopilot.db.migrate import alembic_config

OLD = [
    ("own_post", "manual"),
    ("liked_sample", "oembed"),
    ("competitor_post", "x_api"),
    ("note", "manual"),
    ("file", "manual"),
    ("chat", "radar"),
]


def _insert_old(url: str) -> None:
    [acc] = fetch(url, "INSERT INTO accounts (handle) VALUES ('mig') RETURNING id")
    for i, (kind, origin) in enumerate(OLD):
        fetch(
            url,
            "INSERT INTO sources (account_id, kind, origin, content_hash) VALUES ($1, $2, $3, $4)",
            acc["id"],
            kind,
            origin,
            f"h{i}",
        )


def _rows(url: str, cols: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in fetch(url, f"SELECT {cols} FROM sources ORDER BY content_hash")]


def test_sources_kind_split_up_and_down(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "0003")
    _insert_old(empty_db_url)
    command.upgrade(cfg, "0004")
    assert _rows(empty_db_url, "kind, stance, origin, analysis_status") == [
        ("post", "own", "manual_text", "pending"),
        ("post", "inspiration", "link_oembed", "pending"),
        ("post", "competitor", "x_api", "pending"),
        ("note", "reference", "manual_text", "pending"),
        ("file", "reference", "manual_text", "pending"),
        ("chat", "reference", "radar", "pending"),
    ]
    assert fetch(empty_db_url, "SELECT to_regclass('public.lessons') AS t")[0]["t"] is None
    command.downgrade(cfg, "0003")
    assert _rows(empty_db_url, "kind, origin") == OLD
    assert fetch(empty_db_url, "SELECT to_regclass('public.lessons') AS t")[0]["t"] == "lessons"
    assert fetch(empty_db_url, "SELECT to_regclass('public.authors') AS t")[0]["t"] is None


def test_downgrade_maps_new_kinds_to_old(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "0004")
    [acc] = fetch(empty_db_url, "INSERT INTO accounts (handle) VALUES ('mig2') RETURNING id")
    new = [
        ("reply", "own", "screenshot"),
        ("thread", "anti_example", "tg_forward"),
        ("article", "reference", "url_fetch"),
        ("quote", "competitor", "history_backfill"),
    ]
    for i, (kind, stance, origin) in enumerate(new):
        fetch(
            empty_db_url,
            "INSERT INTO sources (account_id, kind, stance, origin, content_hash)"
            " VALUES ($1, $2, $3, $4, $5)",
            acc["id"],
            kind,
            stance,
            origin,
            f"n{i}",
        )
    command.downgrade(cfg, "0003")
    assert _rows(empty_db_url, "kind, origin") == [
        ("own_post", "manual"),
        ("liked_sample", "manual"),
        ("note", "manual"),
        ("competitor_post", "x_api"),
    ]
