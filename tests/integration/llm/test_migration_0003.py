"""Миграция 0003: ссылка на сущность в llm_calls (вверх и вниз)."""

from alembic import command

from tests.dbutil import fetch
from xcopilot.db.migrate import alembic_config

COLS = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
    " WHERE table_name = 'llm_calls' AND column_name LIKE 'entity%' ORDER BY column_name"
)
INDEX = "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_llm_calls_account_id_entity'"


def test_llm_calls_entity_columns_up_and_down(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "head")
    assert [tuple(r) for r in fetch(empty_db_url, COLS)] == [
        ("entity_id", "uuid", "YES"),
        ("entity_type", "text", "YES"),
    ]
    [idx] = fetch(empty_db_url, INDEX)
    assert "(account_id, entity_type, entity_id)" in idx[0]
    command.downgrade(cfg, "0002")
    assert fetch(empty_db_url, COLS) == []
    assert fetch(empty_db_url, INDEX) == []
    command.upgrade(cfg, "head")
    assert len(fetch(empty_db_url, COLS)) == 2
