# ADR-0016: X — режим `lookup`, бюджет `x_lookup`, подписчики автора, перепроверка

Статус: принято (этап 06d, ветка `stage/06d-x-lookup`, 14.11.2026). Источник: ТЗ v3.1, этап 6, задачи 12–16.

## Решения
1. **Режим `lookup`** — между `owned` и `paid`: по API читаются только посты и профили из присланных
   владельцем ссылок (`XService.lookup_post`, `get_post` в этом режиме делегирует ему). Ленты не
   опрашиваются: `get_own_posts`/`iter_own_history` → `XModeError`. Сбой API или исчерпанный
   бюджет → бесплатный oEmbed.
2. **Бюджет `x_lookup`** (по умолчанию `X_LOOKUP_DAILY_LIMIT_USD=0.5`) считается отдельно от `x_api`.
   В журнале `x_api_calls` добавлена колонка `budget_scope` (`x_api` | `x_lookup`); `SpendRepo.spent`
   суммирует по ней. Итоговый расход X для метрик по-прежнему — сумма всех строк.
3. **Миграция `0006_x_lookup`**: расширены CHECK `accounts.x_read_mode` (+`lookup`) и `budgets.scope`
   (+`x_lookup`), добавлена `x_api_calls.budget_scope`. Откат: `lookup` → `owned`, строки `x_lookup` в
   `budgets` удаляются; колонка журнала снимается.
4. **Подписчики**: `XUser.followers` (из `public_metrics.followers_count`). `XService.get_user` (режимы
   lookup и paid) сначала смотрит `authors.followers*`; в пределах 30 дней API не вызывается. После
   вызова — `authors.followers/followers_observed_at/followers_origin='x_api'`, `x_user_id`, `display_name`.
5. **Своя история**: `XApiV2.iter_own_history(user_id, limit=500)` — асинхронный генератор, страницы по
   запросу; `get_own_posts` стал обёрткой над ним. `XService.iter_own_history` — режимы owned/paid,
   цена как у своих постов ($0.001), 500 постов = $0.50.
6. **Перепроверка**: чтение по API поста моложе 24 часов ставит задачу `x.recheck_metrics`
   (`run_at = created_at + 24h`, ключ идемпотентности по id поста). Обработчик
   `x/recheck.py::handle_recheck_metrics` перечитывает пост и дописывает `material_signals`
   (`origin='x_api'`, `post_age_hours`) материалу с тем же `external_id`. Подключение к воркеру — в
   этапе, который запускает очередь.
7. **Бот**: Настройки → «Доступ к X» → [Точечное чтение] с оценкой «$0.005 за пост, лимит $0.5 — 100
   постов». `test-fix`: тест меню `test_x_access_menu_in_settings` ждал три кнопки, теперь четыре.
