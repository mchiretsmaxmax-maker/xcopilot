## Файлы
- src/xcopilot/metrics/docs.py

## Новые
- (нет)

## Контекст
- src/xcopilot/metrics/catalog.py
- src/xcopilot/db/docs.py
- tests/integration/metrics/test_rollup.py
- src/xcopilot/metrics/definitions/reject_rate_by_reason.sql

## Задача
Реализуй генератор `docs/db/metrics.md` в `src/xcopilot/metrics/docs.py`. Сигнатуры `render_metrics_md(definitions: list[MetricDefinition] | None = None) -> str` и `main(argv: list[str] | None = None, *, root: Path | None = None) -> int` не меняй. Вывод детерминирован, заканчивается ровно одним `\n`.

1. `render_metrics_md(definitions)`: если `definitions is None` — `load_definitions()` (из `xcopilot.metrics.catalog`); метрики сортировать по `key`. Строки через `"\n"`:
   - `# Каталог метрик`, пустая, `> Генерируется командой \`make db-docs\` из \`src/xcopilot/metrics/definitions/*.sql\` (\`xcopilot.metrics.docs\`). Не редактировать вручную.`, пустая, `Метрик: {N}.`, пустая.
   - Короткий раздел `## Как читать`, пустая, текст: `Единицы: \`ratio\` — доля 0..1; \`count\` — штуки; \`micro_usd\` — миллионные доли доллара (BIGINT-деньги); \`minutes\` — минуты. Зерно: \`day\` — день по поясу аккаунта, \`week\` — неделя пн–вс. «Сегодня» считается на лету.`, пустая.
   - `## Сводка`, пустая, таблица `| Ключ | Что показывает | Единица | Зерно | Измерения |` и `|---|---|---|---|---|`; по строке на метрику: `` | `{key}` | {title}. {description если есть} | `{unit}` | `{grain}` | {измерения через ", " в обратных кавычках или "—"} |`` (в ячейках заменять `|` на `\|`, переводы строк на пробел).
   - Затем для каждой метрики раздел: `## \`{key}\``, пустая, `{title}`, пустая, (если есть description) `{description}`, пустая; строка `Единица: \`{unit}\` · зерно: \`{grain}\` · версия: {version}` (если есть измерения — ` · измерения: ` + через ", " в обратных кавычках), пустая; `Формула:`, пустая, блок ```` ```sql ```` + `formula_sql` + ```` ``` ````, пустая.
   - Итог: `"\n".join(lines).rstrip("\n") + "\n"`.
2. `main(argv, *, root=None)`: argparse с флагом `--check`. Корень — `root or Path.cwd()`; файл `docs/db/metrics.md`. Без `--check`: создать папку `docs/db`, записать файл, напечатать `metrics-docs: обновлено`, вернуть 0. С `--check`: если файла нет или содержимое отличается — напечатать в stderr `metrics-docs: устарело, запусти make db-docs` и вернуть 1, иначе 0. Блок `if __name__ == "__main__": raise SystemExit(main())`.
3. Только стандартная библиотека и импорты из `xcopilot.metrics.catalog`. mypy --strict, ruff. Другие файлы не трогать. ВАЖНО: часть тестов этапа сейчас красная по независимым причинам (нет `docs/db/metrics.md`, часть метрик) — не пытайся их чинить; тест `test_metrics_doc_is_up_to_date` пройдёт, когда владелец задачи сгенерирует файл.

## Коммит
feat(metrics): генератор docs/db/metrics.md
