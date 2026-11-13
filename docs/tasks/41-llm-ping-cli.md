## Файлы
- src/xcopilot/llm/cli.py

## Новые
- (нет)

## Контекст
- tests/unit/llm/test_cli.py
- tests/fakes/llm.py
- src/xcopilot/llm/providers/base.py
- src/xcopilot/llm/models_config.py
- src/xcopilot/llm/pricing.py
- src/xcopilot/llm/errors.py
- src/xcopilot/llm/schemas.py
- src/xcopilot/llm/factory.py
- src/xcopilot/llm/roles.py

## Задача
Реализуй в `src/xcopilot/llm/cli.py` функции `ping` и `main`. Класс `PingResult` и сигнатуры не меняй.

1. `async def ping(providers, models, pricing, *, out=print) -> list[PingResult]`:
   - Для каждой роли в порядке `list(Role)` (writer, judge, cheap): `rc = models.roles[role]`, `provider = providers.get(rc.provider)`.
   - Если провайдера нет: результат `PingResult(role, rc.provider, rc.model, ok=False, error="нет ключа")`.
   - Для `Role.JUDGE`: `result = await provider.decide(model=rc.model, state="pong", questions={"pong": NoulQ(instructions="Это слово pong?")})` (используй `typing.cast(DecisionProvider, provider)`); `answer` — строка вида `f"{name}: p={a.p:.2f}"` для первого ответа, если он `NoulA`, иначе `f"{name}: {a.kind}"`; `usage = result.usage`, `model = result.model`.
   - Для остальных ролей: `result = await cast(TextProvider, provider).complete(model=rc.model, request=TextRequest(system=(), messages=(Message("user", "Ответь одним словом: pong"),), max_tokens=16))`; `answer = result.text.strip()[:80]`, `usage = result.usage`, `model = result.model`.
   - Стоимость: `price(rc.provider, model, usage, datetime.now(UTC), table=pricing)`.
   - Ошибки: перехватывай только `ProviderError` и `ProviderUnavailable` (из `xcopilot.llm.errors`). Для `ProviderError` — `error = exc.short()`, для `ProviderUnavailable` — `error = type(exc).__name__`. Результат `ok=False`, `answer=""`, `usage=Usage()`, `cost_micro_usd=0`, `model=rc.model`.
   - Для каждого результата вызови `out(...)` одной строкой:
     - успех: `f"{role.value:<7} {provider_name}/{model}  in={usage.input_tokens} out={usage.output_tokens}  ${cost / 1_000_000:.6f}  → {answer}"`
     - ошибка: `f"{role.value:<7} {provider_name}/{rc.model}  ОШИБКА {error}"`
   - Верни список результатов в том же порядке.
2. `def main(argv: Sequence[str] | None = None) -> int`:
   - `args = list(sys.argv[1:] if argv is None else argv)`.
   - Если `args != ["ping"]`: вывести в stderr `"использование: python -m xcopilot.llm ping"` и вернуть `2`.
   - Иначе: `settings = load_settings()` (из `xcopilot.config`), `results = asyncio.run(ping(build_providers(settings), ModelsConfig.load(), PricingTable.load()))` (`build_providers` из `xcopilot.llm.factory`); вернуть `0`, если все `r.ok`, иначе `1`.
   - Импорт `load_settings` и `build_providers` — внутри `main` (ленивый), чтобы модуль импортировался без .env.
3. Ключи нигде не печатай. Не добавляй зависимостей и других функций. Должны пройти тесты `tests/unit/llm/test_cli.py`.

## Коммит
feat(llm): python -m xcopilot.llm ping
