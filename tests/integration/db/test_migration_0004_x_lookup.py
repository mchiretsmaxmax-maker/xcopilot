"""ТЗ v3.1, дельта этапа 6: режим чтения X `lookup` и лимит `x_lookup` (миграция 0004)."""

import pytest
from alembic import command

from tests.dbutil import fetch
from xcopilot.db.migrate import alembic_config


def test_lookup_mode_and_budget_scope_up_and_down(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "0004")
    [acc] = fetch(
        empty_db_url,
        "INSERT INTO accounts (handle, x_read_mode) VALUES ('lk', 'lookup') RETURNING id",
    )
    fetch(
        empty_db_url,
        "INSERT INTO budgets (account_id, scope, period, limit_micro_usd, hard)"
        " VALUES ($1, 'x_lookup', 'day', 500000, true)",
        acc["id"],
    )
    with pytest.raises(Exception, match="check"):
        fetch(empty_db_url, "INSERT INTO accounts (handle, x_read_mode) VALUES ('b', 'free')")
    # downgrade: lookup → owned, лимиты x_lookup удаляются, старые CHECK возвращаются
    command.downgrade(cfg, "0003")
    [row] = fetch(empty_db_url, "SELECT x_read_mode FROM accounts WHERE handle = 'lk'")
    assert row["x_read_mode"] == "owned"
    assert fetch(empty_db_url, "SELECT 1 FROM budgets WHERE scope = 'x_lookup'") == []
    with pytest.raises(Exception, match="check"):
        fetch(empty_db_url, "INSERT INTO accounts (handle, x_read_mode) VALUES ('c', 'lookup')")
