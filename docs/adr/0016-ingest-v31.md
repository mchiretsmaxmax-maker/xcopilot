# ADR-0016: Ингест по ТЗ v3.1 (этап 7Δ)

Статус: принято (этап 7Δ, 14.11.2026). Дополняет [ADR-0012](0012-ingest.md).

## Контекст
ТЗ v3.1 «Мозг» требует, чтобы скриншот, статья по ссылке, пересланное сообщение и пачка
материалов принимались так же легко, как текст; автор материала — в `authors`; проверка на
входе с карантином; вопрос об отношении вместо «Это что?»; «Убрать материал». Схема — из
02r (`sources.kind/stance/parent_source_id/analysis_status/quarantine_reason/removed_at`,
`authors`, `material_signals`); миграции нет.

## Решения
1. **Vision — роль `cheap` через шлюз.** `deepseek-flash` (V4.1-Flash) принимает картинки в
   Chat Completions. Шлюз получил `Message.images` и `generate(images=…)`: DeepSeek шлёт
   картинку `image_url` с data URL, Anthropic — блоком `image`, `claude_cli` отказывает
   (`PermanentProviderError(kind="images_unsupported")`). Чтение — за `ScreenshotReader`
   (`GatewayScreenshotReader`; `DisabledScreenshotReader` — если vision выключить). Бюджет до
   вызова считает картинку как 800 токенов.
2. **Скриншот** (`origin=screenshot`): картинка лежит в `BlobStore` до чтения; ответ
   `ScreenshotRead` (схема приложения D, `brain/schemas.py`) сохраняется в `meta.screenshot`,
   блоб удаляется — повтор задачи не зовёт модель снова. Текст → документ; `kind` из
   `read.kind` (`thread_part` → `thread`); автор → `authors` (+подписчики, `followers_origin =
   screenshot`); счётчики → одна строка `material_signals` (`origin=screenshot`,
   `read_confidence` = минимум уверенностей). Счётчик с уверенностью < 0.8 → вопрос владельцу с
   двумя прочтениями («1.2K» / «12K») и «Не важно»; ответ — новая строка сигнала с
   `read_confidence = 1` и `confirmed_by`. Ответ/цитата с видимым исходным постом → источник
   исходного поста (`stance=reference`, своя задача разбора, вне партии) и `parent_source_id`.
   «Не пост» → `excluded(not_a_post)` и кнопки [Разобрать как текст] (→ `kind=note`) / [Убрать].
3. **Статья** (`kind=article`, `origin=url_fetch`, `stance=reference`): `safe_get` — только
   http/https без логина в адресе; имя резолвится заранее, все адреса должны быть публичными
   (`is_global`, не multicast/reserved, IPv4-в-IPv6 разворачивается); соединение идёт на
   проверенный IP с исходными `Host` и SNI — подмена DNS между проверкой и запросом не
   помогает; редиректы (до 3) вручную, каждый проверяется заново; `Content-Length` и поток
   обрываются на 2 МБ; принимаются только `text/html`, `xhtml`, `text/plain`. Основной текст —
   trafilatura 2.3 (`favor_precision`). Список утверждений автора и кнопки «взять кандидатов»
   (Б5.2) — этап 7A.
4. **Пересылка Telegram** (`origin=tg_forward`, `stance=inspiration`): автор (`name`,
   `username`, канал) — в `meta.forward_from`; username → `authors.handle` (если похож на
   хэндл). Пересланные ссылки, скрины и файлы идут своими путями с `meta.forward_from`.
5. **Пачки.** `IngestService.accept_batch(payloads)` — одна партия (`meta.batch`, `meta.multi`),
   один ответ «Принял 20 материалов: 14 ссылок, 6 скринов…» и одна сводка «Готово: N разобрано,
   M дублей, K в карантине (…)». `accept()` = пачка из одного входа (прежние ответы).
   В боте пачки собирает `Debouncer(window_s=8)` по ключу (чат, аккаунт, пользователь);
   он в `BotDeps.ingest_batch` (`routers.learn.build_ingest_debouncer(deps)`), без него —
   прежний ответ на каждое сообщение.
6. **Проверка на входе — перед индексацией.** Автор с `trust = -1` → карантин
   `author_blocked` без вызова модели. Свои материалы (`stance=own`) не проверяются. Остальное:
   правило кода (явное обращение к модели: «ignore previous instructions», «системный промпт»…)
   → карантин без модели; затем судья (`gateway.decide`, три вопроса: указания для ИИ ≥ 0.5,
   рискованно ≥ 0.7, «это контент» < 0.2). Карантин = `analysis_status=quarantined` +
   `quarantine_reason`, `status=excluded`, `meta.reason=quarantine`, событие
   `material.quarantined`; куски не пишутся (в ретривал и разбор не попадает). Судья недоступен
   → допуск с `meta.screen=unavailable` (fail-open: материал всё равно идёт в модели только в
   обёртке недоверенных данных; 7A может перепроверить). Итог допуска пишется в `meta.screen` —
   повтор задачи судью не зовёт.
   Ограничение: вставленный владельцем текст по умолчанию `own_post` и проверку не проходит;
   догадка теггера меняет stance уже после проверки.
7. **Отношение** вместо «Это что?»: [Это моё] [Нравится] [Конкурент] [Антипример] [Факты]
   (`irel:<batch>:<stance>`, `set_stance`), ✓ — на преобладающем stance партии; догадка
   теггера для текстов и ссылок осталась; материалы без догадки (скрин, пересылка) — `inspiration`.
   Старые кнопки `ikind:` в истории чата продолжают работать.
8. **«Убрать»** (`irm:<source>`, `irmb:<batch>`): `SourcesRepo.mark_removed(at=clock.now())` —
   отзыв доказательств и пересчёт весов (02r) + событие `material.removed` с числом отозванных.
   Куски остаются; фильтр `removed_at IS NULL` и `analysis_status <> 'quarantined'` в ретривале —
   задача этапа 9 (на будущее).
9. **Репост.** Пост X с текстом `RT @h: …` (так его отдаёт API): материал — исходный пост
   (`kind=post`, автор h, в той же партии) с сигналом `owner_pick`; источник-репост закрывается
   `excluded(repost)` со ссылкой `parent_source_id` на исходный.

## Последствия
- Новая зависимость: trafilatura 2.3.
- `InputKind`: `article`, `screenshot`, `batch`.
- Сигнал `owner_pick` пишется только для репостов; ставить ли его для всех присланных чужих
  материалов — решение этапа 7A.
