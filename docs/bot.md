# Бот (этап 5)

Код: `src/xcopilot/bot/`. Решения: [adr/0010-bot.md](adr/0010-bot.md).

| Что | Где |
|---|---|
| Сборка диспетчера, long polling | `bot/app.py` (`build_dispatcher`, `run_polling`) |
| Зависимости | `bot/deps.py` (`BotDeps`) |
| Middleware | `bot/middlewares/{dedup,errors,flood,auth,account}.py` |
| FSM в PostgreSQL | `bot/storage.py` (`PgStorage`) |
| Роутеры | `bot/routers/{start,help,cost,settings,x_connect,menu,onboarding}.py`, `bot/budget_events.py` |
| Тексты, клавиатуры | `bot/texts.py`, `bot/keyboards.py` |
| Уведомления | `bot/notify.py` (`Notifier.notify`) |

Команды: `/start`, `/help`, `/settings`, `/cost`, `/cancel`. Тесты: `tests/unit/bot`, `tests/integration/bot` (подделка — `tests/fakes/telegram.py`).

Доступ к X (этап 6): Настройки → «Доступ к X» — `bot/routers/x_connect.py`, решения — [adr/0009-x-client.md](adr/0009-x-client.md).
