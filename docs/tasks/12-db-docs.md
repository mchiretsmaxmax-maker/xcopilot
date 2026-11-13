## Файлы
- src/xcopilot/db/docs.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/models/base.py
- src/xcopilot/db/models/access.py
- src/xcopilot/db/models/metrics.py
- tests/unit/db/test_schema_docs.py

## Задача
Реализуй генератор документации схемы в `src/xcopilot/db/docs.py`. Сигнатуры `render_schema_md(metadata: MetaData) -> str` и `render_er_mermaid(metadata: MetaData) -> str` не меняй. Вывод детерминирован (одинаковые метаданные — побайтно одинаковый текст), заканчивается ровно одним `\n`.

1. Вспомогательные функции (приватные, с type hints):
   - `_type(column: Column[Any]) -> str`: `str(column.type.compile(dialect=postgresql.dialect()))` (`from sqlalchemy.dialects import postgresql`).
   - `_default(column: Column[Any]) -> str`: если `column.computed is not None` → `f"генерируется: {column.computed.sqltext}"`; иначе если `column.server_default is not None` и у него есть атрибут `arg` → текст: если `arg` имеет атрибут `text` (TextClause) — `arg.text`, иначе `str(arg)`; иначе `""`.
   - `_cell(value: str) -> str`: заменить `|` на `\|` и переводы строк на пробел.
   - `_tables(metadata)`: `sorted(metadata.tables.values(), key=lambda t: t.name)`.
2. `render_schema_md(metadata)` — строки, соединённые `"\n"`:
   - `# Схема БД`, пустая строка, `> Генерируется командой \`make db-docs\` из моделей (\`xcopilot.db.docs\`). Не редактировать вручную.`, пустая строка, `Таблиц: {N}.`, пустая строка.
   - `## Оглавление`, пустая строка, для каждой таблицы `- \`{имя}\` — {comment таблицы}`, пустая строка.
   - Для каждой таблицы (по имени):
     - `## \`{имя}\``, пустая строка, `{table.comment}`, пустая строка;
     - заголовок `| Колонка | Тип | NULL | По умолчанию | Описание |` и `|---|---|---|---|---|`;
     - для каждой колонки в порядке `table.columns`: `| \`{name}\` | {_type} | {"да" если nullable иначе "нет"} | {"`" + d + "`" если d иначе ""} | {_cell(comment or "")} |` где `d = _cell(_default(col))`;
     - пустая строка; `Первичный ключ: ` + колонки PK через `, ` в обратных кавычках (`table.primary_key.columns`);
     - если есть внешние ключи (`sorted(table.foreign_keys, key=lambda fk: fk.parent.name)`): строка `Внешние ключи: ` + элементы `` `{fk.parent.name}` → `{fk.column.table.name}.{fk.column.name}` `` через `, `;
     - если есть индексы (`sorted(table.indexes, key=lambda i: i.name or "")`): строка `Индексы:` и для каждого `- \`{index.name}\` ({колонки или выражения через ", "})` + ` уникальный` если `index.unique` + ` {using}` если `index.dialect_options["postgresql"]["using"]` задан; колонки индекса — `", ".join(c.name for c in index.columns)`, а если `index.columns` пуст — `", ".join(str(e) for e in index.expressions)`;
     - если `table.dialect_options["postgresql"]["partition_by"]` задан: строка `Партиции: \`{значение}\` по месяцам + DEFAULT.`;
     - пустая строка.
   - Итог: `"\n".join(lines).rstrip("\n") + "\n"`.
3. `render_er_mermaid(metadata)`:
   - Строки: `# ER-диаграмма`, пустая, `> Генерируется командой \`make db-docs\` из моделей (\`xcopilot.db.docs\`). Не редактировать вручную.`, пустая, ` ```mermaid` (три обратные кавычки + mermaid, без пробела), `erDiagram`.
   - Для каждой таблицы: `    {имя} {`; для каждой колонки `        {mtype} {name}{keys}`, где `mtype = re.sub(r"[^a-z0-9]+", "_", _type(col).lower()).strip("_")`, `keys` — `" PK"`, `" FK"` или `" PK, FK"` (PK — `col.primary_key`, FK — `bool(col.foreign_keys)`), иначе `""`; затем `    }`.
   - Затем связи: множество кортежей `(fk.column.table.name, table.name, fk.parent.name)` по всем таблицам и внешним ключам, отсортированное; строка `f'    {родитель} ||--o{{ {ребёнок} : "{колонка}"'`, пример: `    accounts ||--o{ sources : "account_id"`.
   - Закрыть ` ``` `, итог `"\n".join(lines) + "\n"`.
4. CLI: `def main(argv: list[str] | None = None) -> int`: аргумент `--check` (argparse). Корень — `Path.cwd()`. Файлы `docs/db/schema.md` и `docs/db/er.md` (создать папку). Без `--check` — записать оба файла (`write_text`), напечатать `db-docs: обновлено`, вернуть 0. С `--check` — сравнить текущее содержимое (если файла нет — считать устаревшим), при расхождении напечатать в stderr `db-docs: устарело, запусти make db-docs` и вернуть 1, иначе 0. Метаданные — `from xcopilot.db.models import Base`, `Base.metadata` (импорт внутри `main`). Блок `if __name__ == "__main__": raise SystemExit(main())`.
5. Только стандартная библиотека и sqlalchemy. Никаких других функций и зависимостей. mypy --strict, ruff.
6. Тест `test_schema_doc_is_up_to_date` упадёт, пока файлы не сгенерированы — это нормально, файлы сгенерирует Claude. Проверка запускает только `tests/unit/test_imports.py`.

## Коммит
feat(db): генератор docs/db/schema.md и ER-диаграммы
