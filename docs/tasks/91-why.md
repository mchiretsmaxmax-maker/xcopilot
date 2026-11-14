## Файлы
- src/xcopilot/bot/routers/why.py
- src/xcopilot/bot/texts.py

## Новые
- (нет)

## Контекст
- tests/integration/bot/test_why.py
- src/xcopilot/bot/routers/cost.py
- src/xcopilot/retrieval/context_pack.py
- src/xcopilot/db/repos/runs.py
- src/xcopilot/db/repos/prompts.py

## Задача
Python 3.13, mypy --strict, docstrings на русском. Образец роутера — `cost.py`.

### 1. `src/xcopilot/bot/texts.py` — ТОЛЬКО ДОБАВЬ в конец файла, остальное не трогай (верни файл целиком, с неизменёнными прежними строками):
```python
WHY_EMPTY = "Пока нет ни одного запуска генерации — смотреть нечего."
WHY_ITEM_LIMIT = 80
WHY_TEXT_LIMIT = 4000


def _clip(value: str, limit: int = WHY_ITEM_LIMIT) -> str:
    """Одна строка не длиннее limit знаков: переводы строк → пробелы, хвост → «…»."""


def why_report(
    *,
    passport_version: int | None,
    prompt_label: str | None,
    tokens: int,
    budget_tokens: int,
    examples: list[str],
    lessons: list[str],
    facts: list[str],
    anti_examples: list[str],
    references: list[str],
    warnings: list[str],
) -> str:
    """Отчёт /why: что писатель видел в последнем запуске."""
```
Формат `why_report` (строки через "\n"):
- `Паспорт: v{passport_version}` (если None — `Паспорт: нет`);
- `Промпт: {prompt_label}` (если None — `Промпт: —`);
- `Пакет: {tokens} из {budget_tokens} токенов`;
- затем для каждого из пяти списков, КРОМЕ пустых, — пустая строка и блок: заголовок `Примеры голоса ({n}):` / `Уроки ({n}):` / `Факты ({n}):` / `Антипримеры ({n}):` / `Референсы ({n}):` (именно в таком порядке), а под ним по строке на элемент: для примеров, антипримеров и референсов `{i}. {_clip(item)}` (i с 1), для уроков и фактов `• {_clip(item)}`;
- если warnings не пустой — пустая строка и `Предупреждения: ` + ", ".join(warnings);
- весь результат обрезать до `WHY_TEXT_LIMIT` знаков (`[:WHY_TEXT_LIMIT]`).
`_clip`: заменить `\n` на пробел, `strip()`; если длиннее `limit`, вернуть первые `limit - 1` знаков + "…".

### 2. `src/xcopilot/bot/routers/why.py` — замени целиком
`def build_router() -> Router` (внутри `router = Router(name="why")`, возвращает router). Обработчик `@router.message(Command("why"))`, `async def cmd_why(message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder) -> None`:
1. `await rec("why")` — первым делом.
2. `acc is None` → `await message.answer(texts.NO_ACCOUNT)`; return.
3. `async with deps.uow_factory() as uow:` — `run = await RunsRepo(uow.session, acc.id).latest()`; если `None` → после блока ответить `texts.WHY_EMPTY` и return. Иначе: `ids = PackIds.model_validate(run.context_pack)`; `pack = await rehydrate(uow.session, acc.id, ids)`; если `run.prompt_id is not None` — `prompt = await PromptsRepo(uow.session).get(run.prompt_id)` и `prompt_label = f"{prompt.key} v{prompt.version}"` (если prompt None или prompt_id None — `prompt_label = None`). Всё нужное копируй в локальные переменные внутри блока.
4. Ответ: `await message.answer(texts.why_report(...))`, где `passport_version=pack.passport_version`, `tokens=pack.tokens`, `budget_tokens=pack.budget_tokens`, `examples=[c.text for c in pack.examples]`, `lessons=[x.text for x in pack.lessons]`, `facts=[f.statement for f in pack.facts]`, `anti_examples=pack.anti_examples`, `references=[c.text for c in pack.references]`, `warnings=pack.warnings`.
Импорты: `from xcopilot.db.repos import PromptsRepo, RunsRepo`; `from xcopilot.retrieval.context_pack import PackIds, rehydrate`. `message.answer` без parse_mode.

Должны пройти тесты `tests/integration/bot/test_why.py` (rehydrate пока может быть заглушкой — тогда ожидаемо красные; проверяй mypy/ruff).

## Коммит
feat(bot): /why — что писатель видел в последнем запуске
