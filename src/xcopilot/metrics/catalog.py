"""Каталог метрик: разбор definitions/*.sql и синхронизация в metric_definitions.

Файл: шапка из строк `-- ключ: значение` (на одной строке можно несколько через « · »),
затем запрос `SELECT period_start, dims, value, sample_size`. Обязательные поля шапки:
key (равен имени файла), title, unit, grain, version; необязательные: description, dims.
Версия повышается при любом изменении формулы — это проверяется по хэшу.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from xcopilot.db.repos import MetricDefsRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.errors import CatalogError, FormulaVersionError

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
UNITS = ("ratio", "count", "micro_usd", "minutes")
GRAINS = ("day", "week")
REQUIRED_COLUMNS = ("period_start", "dims", "value", "sample_size")
_HEADER_KEYS = {"key", "title", "description", "unit", "grain", "version", "dims"}
_REQUIRED_HEADER = ("key", "title", "unit", "grain", "version")


class MetricDefinition(BaseModel):
    key: str
    title: str
    description: str | None = None
    unit: str
    grain: str
    version: int
    dimensions: list[str] = []
    formula_sql: str
    formula_hash: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    deactivated: list[str]


def formula_hash(sql: str) -> str:
    """Хэш формулы без учёта пробелов и переводов строк."""
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()


def _parse(path: Path) -> MetricDefinition:
    where = path.name
    lines = path.read_text().splitlines()
    header: dict[str, str] = {}
    body_start = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("--"):
            if stripped:
                body_start = index
                break
            continue
        for part in stripped[2:].split("·"):
            name, sep, value = part.partition(":")
            name = name.strip().lower()
            if sep and name in _HEADER_KEYS:
                header[name] = value.strip()
    missing = [k for k in _REQUIRED_HEADER if k not in header]
    if missing:
        raise CatalogError(f"{where}: в шапке нет полей {', '.join(missing)}")
    if header["key"] != path.stem:
        raise CatalogError(f"{where}: key {header['key']!r} должен совпадать с именем файла")
    if header["unit"] not in UNITS:
        raise CatalogError(f"{where}: unit должен быть один из {', '.join(UNITS)}")
    if header["grain"] not in GRAINS:
        raise CatalogError(f"{where}: grain должен быть day или week")
    if not header["version"].isdigit() or int(header["version"]) < 1:
        raise CatalogError(f"{where}: version — целое число от 1")
    sql = "\n".join(lines[body_start:]).strip().rstrip(";").strip()
    if not re.match(r"(?is)\A(select|with)\b", sql):
        raise CatalogError(f"{where}: формула должна начинаться с SELECT или WITH")
    if ";" in sql:
        raise CatalogError(f"{where}: в формуле должен быть один запрос")
    absent = [c for c in REQUIRED_COLUMNS if not re.search(rf"\b{c}\b", sql)]
    if absent:
        raise CatalogError(f"{where}: формула не возвращает колонки {', '.join(absent)}")
    return MetricDefinition(
        key=header["key"],
        title=header["title"],
        description=header.get("description"),
        unit=header["unit"],
        grain=header["grain"],
        version=int(header["version"]),
        dimensions=[d.strip() for d in header.get("dims", "").split(",") if d.strip()],
        formula_sql=sql,
        formula_hash=formula_hash(sql),
    )


def load_definitions(path: Path | None = None) -> list[MetricDefinition]:
    """Разобрать все `*.sql` каталога (по умолчанию — metrics/definitions), по ключу."""
    folder = path or DEFINITIONS_DIR
    return [_parse(p) for p in sorted(folder.glob("*.sql"))]


def _fields(d: MetricDefinition) -> dict[str, object]:
    return {
        "version": d.version,
        "title": d.title,
        "description": d.description,
        "unit": d.unit,
        "grain": d.grain,
        "dimensions": d.dimensions,
        "formula_sql": d.formula_sql,
        "formula_hash": d.formula_hash,
        "active": True,
    }


async def sync_catalog(session: DbSession, path: Path | None = None) -> SyncResult:
    """Привести metric_definitions в соответствие файлам.

    Формула изменилась, а версия не выросла — `FormulaVersionError`. Метрики, чьих файлов
    больше нет, помечаются неактивными (свёртки остаются). Повторный запуск ничего не меняет.
    """
    definitions = load_definitions(path)
    repo = MetricDefsRepo(session)
    existing = {row.key: row for row in await repo.all()}
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for d in definitions:
        row = existing.get(d.key)
        if row is None:
            await repo.add(key=d.key, **_fields(d))
            created.append(d.key)
            continue
        if d.formula_hash != row.formula_hash and d.version <= row.version:
            raise FormulaVersionError(
                f"{d.key}: формула изменилась, повысь version (сейчас {row.version})"
            )
        if d.version < row.version:
            raise FormulaVersionError(f"{d.key}: version {d.version} ниже записанной {row.version}")
        fields = _fields(d)
        if all(getattr(row, name) == value for name, value in fields.items()):
            unchanged.append(d.key)
            continue
        await repo.update(d.key, **fields)
        updated.append(d.key)
    deactivated = [
        key
        for key, row in existing.items()
        if row.active and key not in {d.key for d in definitions}
    ]
    for key in deactivated:
        await repo.update(key, active=False)
    return SyncResult(created, updated, unchanged, deactivated)
