# xcopilot

AI co-pilot для X: Telegram-бот, который учится голосу аккаунта на примерах и правках, готовит посты и ответы, проверяет их кодом и судьёй, а публикует только человек.

- ТЗ: [docs/architecture-v3.1-plan.md](docs/architecture-v3.1-plan.md)
- Обзор проекта: [docs/overview.md](docs/overview.md)
- Статус и очередь этапов: [docs/README.md](docs/README.md)
- Правила работы: [CLAUDE.md](CLAUDE.md)

Стек: Python 3.13, aiogram 3, PostgreSQL 18 + pgvector, Claude / DeepSeek / Jev, Docker Compose.

## Быстрый старт

1. `cp .env.example .env`
2. Заполнить в `.env`: `BOT_TOKEN`, `OWNER_TG_IDS`, `SECRETS_KEY`, `DEEPSEEK_API_KEY`.
3. `make up` — поднимет БД, накатит миграции, запустит бота.
4. Написать боту `/start`.

Тесты — `make test` (нужен Docker). Писатель в контейнере пока не работает без решения из раздела G плана аудита ([docs/audit-fix-plan.md](docs/audit-fix-plan.md)). Локальный запуск без Docker — [docs/runbook.md](docs/runbook.md), §10. Запуск на устройстве как служба и перенос на другое устройство (`make setup`, `make export`, `make import FILE=…`) — [runbook §12](docs/runbook.md).
