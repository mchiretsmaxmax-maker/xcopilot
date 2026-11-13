"""Документация схемы генерируется из моделей и лежит в репозитории актуальной."""

from pathlib import Path

from xcopilot.db.docs import render_er_mermaid, render_schema_md
from xcopilot.db.models import Base

ROOT = Path(__file__).resolve().parents[3]


def test_schema_doc_is_up_to_date() -> None:
    schema = render_schema_md(Base.metadata)
    er = render_er_mermaid(Base.metadata)
    assert "## `drafts`" in schema and "`account_id`" in schema
    assert "```mermaid" in er and "erDiagram" in er
    assert (ROOT / "docs/db/schema.md").read_text() == schema, "запусти make db-docs"
    assert (ROOT / "docs/db/er.md").read_text() == er, "запусти make db-docs"
