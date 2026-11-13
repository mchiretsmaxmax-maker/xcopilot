"""Слои: SQL и модели живут только в пакете xcopilot.db (репозитории, миграции, партиции).

Критерий готовности этапа 2: ни один модуль вне db не содержит SQL и не импортирует
модели напрямую. Прикладной код ходит в БД только через xcopilot.db.repos и UnitOfWork.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "xcopilot"
DB = SRC / "db"
FORBIDDEN_IMPORTS = ("sqlalchemy", "asyncpg", "alembic", "pgvector", "xcopilot.db.models")
SQL = re.compile(
    r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE)\b",
    re.S,
)


def _imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


def test_sql_and_models_only_in_db_package() -> None:
    problems: list[str] = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        if path.is_relative_to(DB):
            continue
        checked += 1
        rel = path.relative_to(SRC.parent)
        tree = ast.parse(path.read_text())
        for name in _imports(tree):
            if name.startswith(FORBIDDEN_IMPORTS):
                problems.append(f"{rel}: импорт {name}")
        for node in ast.walk(tree):
            is_str = isinstance(node, ast.Constant) and isinstance(node.value, str)
            if is_str and SQL.search(node.value):
                problems.append(f"{rel}: SQL в строке {node.value[:40]!r}")
    assert checked >= 5
    assert problems == []


def test_models_imported_only_by_repos_and_schema_tools() -> None:
    allowed = {"repos", "models", "migrations", "partitions.py", "docs.py", "migrate.py"}
    problems: list[str] = []
    for path in sorted(DB.rglob("*.py")):
        top = path.relative_to(DB).parts[0]
        if top in allowed:
            continue
        for name in _imports(ast.parse(path.read_text())):
            if name.startswith("xcopilot.db.models"):
                problems.append(f"{path.relative_to(SRC.parent)}: импорт {name}")
    assert problems == []
