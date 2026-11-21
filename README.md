# xcopilot

AI co-pilot для X: Telegram-бот, который учится голосу аккаунта на примерах и правках, готовит посты и ответы, проверяет их кодом и судьёй, а публикует только человек.

- ТЗ: [docs/architecture-v3.1-plan.md](docs/architecture-v3.1-plan.md)
- Обзор проекта: [docs/overview.md](docs/overview.md)
- Статус и очередь этапов: [docs/README.md](docs/README.md)
- Правила работы: [AGENTS.md](AGENTS.md)

Стек: Python 3.13, aiogram 3, PostgreSQL 18 + pgvector, Codex CLI / DeepSeek / Jev, Docker Compose.

## Быстрый старт

1. Установить Docker, uv и [Codex CLI](https://learn.chatgpt.com/docs/cli) на устройство, где будет работать бот.
2. Выполнить `codex login` и войти в ChatGPT под пользователем службы.
3. Скопировать `.env.example` в `.env`, заполнить `BOT_TOKEN`, `OWNER_TG_IDS`, `SECRETS_KEY` и `DEEPSEEK_API_KEY`. При необходимости задать абсолютный `CODEX_CLI_PATH`.
4. Выполнить `make setup` и написать боту `/start`. Бот запускается на хосте, PostgreSQL — в Docker.

Писатель использует GPT‑6.2 Sol с усилием `medium`. Модель задаётся в `config/models.toml`, усилие — через `CODEX_CLI_EFFORT`. Тесты — `make test` (нужен Docker). Запуск и перенос на другое устройство описаны в [runbook](docs/runbook.md).
