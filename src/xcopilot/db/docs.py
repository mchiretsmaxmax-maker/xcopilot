"""Генерация docs/db/schema.md и docs/db/er.md из метаданных моделей."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Column, MetaData
from sqlalchemy.dialects import postgresql

_DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]  # у SQLAlchemy нет аннотации


def _type(column: Column[Any]) -> str:
    return str(column.type.compile(dialect=_DIALECT))


def _default(column: Column[Any]) -> str:
    if column.computed is not None:
        return f"генерируется: {column.computed.sqltext}"
    if column.server_default is not None and hasattr(column.server_default, "arg"):
        arg = column.server_default.arg
        if hasattr(arg, "text"):
            return cast(str, arg.text)
        return str(arg)
    return ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _tables(metadata: MetaData) -> list[Any]:
    return sorted(metadata.tables.values(), key=lambda t: t.name)


def render_schema_md(metadata: MetaData) -> str:
    """Markdown: таблица -> назначение -> колонки с описанием."""
    tables = _tables(metadata)
    lines: list[str] = [
        "# Схема БД",
        "",
        (
            "> Генерируется командой `make db-docs` из моделей "
            "(`xcopilot.db.docs`). Не редактировать вручную."
        ),
        "",
        f"Таблиц: {len(tables)}.",
        "",
        "## Оглавление",
        "",
    ]
    for table in tables:
        lines.append(f"- `{table.name}` — {table.comment}")
    lines.append("")

    for table in tables:
        lines.extend(
            [
                f"## `{table.name}`",
                "",
                table.comment or "",
                "",
                "| Колонка | Тип | NULL | По умолчанию | Описание |",
                "|---|---|---|---|---|",
            ]
        )
        for column in table.columns:
            d = _cell(_default(column))
            default = f"`{d}`" if d else ""
            nullable = "да" if column.nullable else "нет"
            row = (
                f"| `{column.name}` | {_type(column)} | {nullable} | "
                f"{default} | {_cell(column.comment or '')} |"
            )
            lines.append(row)
        lines.append("")
        pk = ", ".join(f"`{c.name}`" for c in table.primary_key.columns)
        lines.append(f"Первичный ключ: {pk}")
        foreign_keys = sorted(
            table.foreign_keys,
            key=lambda fk: (fk.parent.name, fk.column.table.name, fk.column.name),
        )
        if foreign_keys:
            fk_parts = [
                f"`{fk.parent.name}` → `{fk.column.table.name}.{fk.column.name}`"
                for fk in foreign_keys
            ]
            lines.append("Внешние ключи: " + ", ".join(fk_parts))
        indexes = sorted(table.indexes, key=lambda i: i.name or "")
        if indexes:
            lines.append("Индексы:")
            for index in indexes:
                if index.columns:
                    columns = ", ".join(c.name for c in index.columns)
                else:
                    columns = ", ".join(str(e) for e in index.expressions)
                unique = " уникальный" if index.unique else ""
                using = index.dialect_options["postgresql"].get("using")
                using_part = f" {using}" if using else ""
                lines.append(f"- `{index.name}` ({columns}){unique}{using_part}")
        partition_by = table.dialect_options["postgresql"].get("partition_by")
        if partition_by:
            lines.append(f"Партиции: `{partition_by}` по месяцам + DEFAULT.")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_er_mermaid(metadata: MetaData) -> str:
    """Markdown с ER-диаграммой Mermaid."""
    tables = _tables(metadata)
    lines: list[str] = [
        "# ER-диаграмма",
        "",
        (
            "> Генерируется командой `make db-docs` из моделей "
            "(`xcopilot.db.docs`). Не редактировать вручную."
        ),
        "",
        "```mermaid",
        "erDiagram",
    ]
    for table in tables:
        lines.append(f"    {table.name} {{")
        for column in table.columns:
            mtype = re.sub(r"[^a-z0-9]+", "_", _type(column).lower()).strip("_")
            keys = ""
            if column.primary_key and column.foreign_keys:
                keys = " PK, FK"
            elif column.primary_key:
                keys = " PK"
            elif column.foreign_keys:
                keys = " FK"
            lines.append(f"        {mtype} {column.name}{keys}")
        lines.append("    }")

    relations: set[tuple[str, str, str]] = set()
    for table in tables:
        for fk in table.foreign_keys:
            relations.add((fk.column.table.name, table.name, fk.parent.name))
    for parent, child, column in sorted(relations):
        lines.append(f'    {parent} ||--o{{ {child} : "{column}"')
    lines.append("```")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    from xcopilot.db.models import Base

    root = Path.cwd()
    docs_dir = root / "docs" / "db"
    schema_path = docs_dir / "schema.md"
    er_path = docs_dir / "er.md"

    schema = render_schema_md(Base.metadata)
    er = render_er_mermaid(Base.metadata)

    if args.check:
        current_schema = schema_path.read_text() if schema_path.exists() else None
        current_er = er_path.read_text() if er_path.exists() else None
        if current_schema != schema or current_er != er:
            print("db-docs: устарело, запусти make db-docs", file=sys.stderr)
            return 1
        return 0

    docs_dir.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(schema)
    er_path.write_text(er)
    print("db-docs: обновлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
