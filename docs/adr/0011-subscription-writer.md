# ADR-0011: писатель на подписке Claude, судья на DeepSeek

Статус: принято (13.11.2026). Дополняет ADR-0002 и ADR-0007.

## Контекст
Денег на Anthropic API и Jev пока нет. Есть подписка Claude у владельца и ключ DeepSeek.
Адаптеры Anthropic и Jev остаются в коде нетронутыми: вернуться к ним нужно одной строкой.

## Решение
- **Провайдер `claude_cli`** (`llm/providers/claude_cli.py`) — `TextProvider`, вызывает
  `claude -p --output-format json --model M --system-prompt S --max-turns 1 --tools ""`
  (плюс `--no-session-persistence`, `--disable-slash-commands`, `--strict-mcp-config`,
  `--setting-sources ""`: без сессий, команд и настроек/CLAUDE.md пользователя). Промпт — stdin.
  Запуск через внедряемый `CliRunner` (в тестах — подделка); настоящий — `asyncio` subprocess
  с таймаутом и `kill`. Бинарь, таймаут и параллельность — `CLAUDE_CLI_PATH` (`claude`),
  `CLAUDE_CLI_TIMEOUT` (180 с), `CLAUDE_CLI_CONCURRENCY` (1, семафор).
- **Окружение процесса** — копия без `ANTHROPIC_API_KEY` и `ANTHROPIC_AUTH_TOKEN`: иначе CLI
  пошёл бы в платный API. Провайдер строится всегда (ключ не нужен).
- **Ошибки.** Лимит подписки («hit your limit», rate limit, 429), ненулевой код выхода →
  `SubscriptionLimit` (наследует `TransientProviderError` и `ProviderUnavailable`): шлюз повторяет,
  размыкает предохранитель и отдаёт `ProviderUnavailable` (у writer замены нет → очередь).
  Таймаут → временная; нет бинаря, не залогинен, прочие ошибки CLI, мусор → постоянная.
  Тексты ошибок — фиксированные метки, вывод CLI и окружение не попадают никуда.
- **Стоимость 0**: `[claude_cli.default]` в `config/pricing.toml`; `llm_calls` хранит токены
  (включая кэш) и задержку, бюджет не расходуется.
- **Судья на DeepSeek.** Если провайдер роли `judge` не умеет `decide` (текстовая модель),
  `gateway.decide` идёт тем же путём, что резервный судья: промпт `judge.fallback`, разбор в ту же
  схему `Verdict`. Флаг: `fallback=False` — это настоящий основной судья; модель видно в
  `Verdict.judge` (`deepseek-flash`). Карточка может показать «судья: DeepSeek» по этому полю.
  Резерва у такого судьи нет: недоступен → `ProviderUnavailable`.
- **`config/models.toml`**: writer → `claude_cli` / `claude-opus-5-5`, judge → `deepseek` /
  `deepseek-flash`; строки anthropic/jev закомментированы «вернуть, когда будут ключи».

## Как вернуть Anthropic и Jev
В `config/models.toml`: у `writer` — `provider = "anthropic"`; у `judge` — `provider = "jev"`,
`model = "jev-latest"` (затем закрепить версию из `GET /v1/models`). Ключи — в `.env`.
Код и цены менять не нужно. Прежняя раскладка лежит в `tests/fixtures/models_anthropic_jev.toml`.

## Риски
- Лимиты подписки общие с разработкой (интерактивные сессии владельца): при исчерпании писатель
  встаёт, вызовы уходят в очередь с повтором.
- На сервере нужен залогиненный `claude` (`claude login` под тем же пользователем); без него —
  постоянная ошибка `not_logged_in`.
- Параллельность: по умолчанию один вызов за раз (семафор) — писатель медленнее API.
- Каждый вызов тянет ~1k токенов служебного системного промпта CLI; задержка 3–10 с.
- Использование подписки для автоматизации — на условиях Anthropic; при отказе — вернуть API.
