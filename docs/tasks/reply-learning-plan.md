# План: самообучение на ответах (ADR 0034)

Дизайн: [docs/adr/0034-reply-self-learning.md](../adr/0034-reply-self-learning.md). Стартовать после слияния `feat/fxembed-v2` в `main`: этапы 3–4 зависят от `fetch_replies` / `fetch_quotes`. Этап R2 (схема и веса) от FxEmbed не зависит, его можно делать сразу.

Порядок: **R2 → R3 → R4** (R1 — по результату fxembed-v2, возможно не нужен). Каждый этап — своя ветка `feat/reply-learning-NN-…` от `main`, TDD по ТЗ 1.0: первый коммит — только тесты ниже плюс пустые заглушки, тег `reply-learning-NN-red`.

Общий критерий готовности: зелёные `ruff`, `mypy`, весь `pytest`. Чужие тексты не попадают ни в примеры голоса, ни в паспорт (тесты R3 №9–11). Владелец не получает ни одного сообщения от сбора, кроме существующего «🧠 Выучила…».

---

## R1. Поля клиента FxEmbed (если fxembed-v2 их не дал) — XS

**Модель:** Sonnet, low (или задание DeepSeek `deepseek-chat` без thinking: 1–2 файла, всё явно).
**Файлы:** `src/xcopilot/x/public_read.py` (или модуль клиента v2 из fxembed-v2), `src/xcopilot/x/models.py`, тесты клиента.
**Что:** у `XPost` из `fetch_replies` / `fetch_quotes` есть `lang`, `replying_to`, `created_at`, `author_followers`, `metrics.likes`. Общий ограничитель на 300 запросов/мин (токен-ведро в клиенте, часы внедряются).

**Тесты (до кода):**
1. `test_reply_post_has_lang_and_replying_to` — запись ответа `conversation` из фикстуры → `lang == "en"`, `replying_to == <id поста>`.
2. `test_reply_missing_lang_is_none` — поля нет → `None`, без исключения.
3. `test_rate_limiter_waits_after_limit` — 301-й запрос в той же минуте ждёт (фейковые часы и sleep).
4. `test_429_raises_unavailable` — ответ 429 → `XUnavailable`.

---

## R2. Схема и независимость доказательств — M, ядро весов

**Модель:** Opus, medium (миграция, SQL-функция весов, правила жизненного цикла; DeepSeek сюда нельзя: схема БД).
**Ветка:** `feat/reply-learning-02-contexts`.
**Файлы:** `src/xcopilot/db/migrations/versions/0013_reply_harvest.py` (новая), `src/xcopilot/db/models/*` (поля `context_key`, `independent_contexts`), `src/xcopilot/brain/evidence.py` (`material_evidence`: `context_key` и `w_author = min(1/k_author, 1/k_context)`), `src/xcopilot/db/repos/brain.py` (счётчик доказательств по контексту; `pending_materials` и `batch_materials` отдают `context_key`), `src/xcopilot/brain/extract.py` (`min_supporting_contexts`, `per_context_in_batch`, минимум 3 контекста для пачки сбора), `src/xcopilot/brain/lifecycle.py` (`probation_min_contexts`), `src/xcopilot/brain/config.py` и `config/brain.toml`, `docs/db/` (описание колонок).
**Миграция:** ровно по разделу «Миграция 0013» ADR, пп. 1–6. `context_key` материала — `sources.meta.harvest` (id исходного поста), иначе NULL.

**Тесты (до кода):**
1. `test_0013_upgrade_downgrade` — апгрейд и даунгрейд на чистой и на заполненной базе; после даунгрейда нет `reply_harvest` и `sibling_rank`.
2. `test_origin_reply_harvest_allowed` — вставка `sources.origin = 'reply_harvest'` проходит; неизвестный origin — ошибка CHECK.
3. `test_basis_sibling_rank_allowed` — `material_outcomes.basis = 'sibling_rank'` проходит.
4. `test_evidence_context_key_from_harvest_meta` — материал с `meta.harvest = "123"` → доказательство с `context_key = "123"`; обычный материал → NULL.
5. `test_w_author_discounts_same_context` — 3 материала разных авторов с одним `context_key` по одному знанию → `w_author` = 1, 0.5, 0.333.
6. `test_w_author_takes_min_of_author_and_context` — 2-й от того же автора в новом контексте → 0.5; 3-й в том же контексте от нового автора → 1/k_context.
7. `test_own_material_not_discounted` — свой пост: `w_author = 1` при любом k (как сейчас).
8. `test_recompute_counts_independent_contexts` — 5 доказательств: 4 из контекста A и 1 без ключа → `independent_contexts = 2`.
9. `test_extract_rejects_single_context_support` — кандидат, все `supporting` которого из одного контекста → не создан; причина в отчёте `single_context`.
10. `test_extract_accepts_two_contexts` — подтверждения из 2 контекстов → кандидат создан.
11. `test_batch_caps_per_context` — 30 разборов одного контекста + 10 из двух других → в пачке ≤ 8 из одного контекста.
12. `test_harvest_batch_waits_for_three_contexts` — только 2 контекста сбора, младше суток → пачка не собирается; через сутки — собирается.
13. `test_probation_needs_two_contexts` — знание с 3 доказательствами от 3 авторов, но из одного контекста → остаётся `candidate`; добавили второй контекст → `probation`.
14. `test_old_evidence_unchanged` — веса существующих доказательств (NULL `context_key`) после миграции и пересчёта не меняются (сравнение до и после на фикстуре).

---

## R3. Сбор ответов и цитат — L, основной

**Модель:** Opus, medium. Чистые функции (фильтр, отбор) можно отдать DeepSeek отдельными заданиями `docs/tasks/rl-3a-filter.md`, `rl-3b-select.md` (`deepseek-chat`, thinking для отбора). Задания пишет исполнитель R3 после красных тестов.
**Ветка:** `feat/reply-learning-03-harvest`.
**Файлы (новые):** `src/xcopilot/brain/harvest.py` (`junk_reason(post, ctx) -> str | None`, `select(replies, parent) -> Selection` (топ + контраст), `sibling_tier(likes, parent_likes, rank) -> str`, `Harvester.run(account_id, payload) -> HarvestResult`), `config/harvest.toml`, `tests/unit/brain/test_harvest_filter.py`, `tests/unit/brain/test_harvest_select.py`, `tests/integration/brain/test_harvest_job.py`.
**Файлы (правка):** `src/xcopilot/brain/jobs.py` (`HARVEST_KIND = "brain.harvest_replies"`, `RECHECK_KIND = "brain.harvest_recheck"`, `enqueue_harvest`, `handle_harvest`, `handle_recheck`), `src/xcopilot/brain/outcomes.py` (`basis = sibling_rank` для материалов с `meta.harvest`; без отсрочки 24 ч), `src/xcopilot/ingest/service.py` (тихие материалы: `_maybe_summarize` молчит, `_guess_kind` не переписывает `reply_harvest`; запись через существующий `add`), `src/xcopilot/brain/batch.py` (тихие материалы не входят в сводку пачки и карточки), `src/xcopilot/bot/routers/replies.py` и `write.py` (постановка задачи), `src/xcopilot/app.py` (регистрация обработчика, быстрая полоса), настройка `reply_learning` (`bot/routers/settings.py`), строка в `/status`.
**Конфиг `config/harvest.toml`:** `recheck_hours = 24`, `scan_max = 100`, `scan_max_quotes = 40`, `per_post = 20`, `per_post_quotes = 5`, `contrast = 5`, `contrast_window_min = 120`, `min_likes = 3`, `min_chars = 25`, `min_words = 4`, `min_author_followers = 50`, `langs = ["en"]`, `bot_handles = ["grok", "AskPerplexity", …]`, `spam_phrases = […]`, `daily_max_materials = 500`; `config/outcomes.toml [sibling]`: `hit_share = 0.05`, `above_share = 0.01`, `hit_top = 3`, `hit_top_min_likes = 20`.

**Тесты (до кода):**

Фильтр (`junk_reason`):
1. `test_junk_short_after_strip` — «@a @b 🔥🔥 so true» → `short`; осмысленные 6 слов → `None`.
2. `test_junk_link_or_ticker_or_promo` — ссылка, `$PEPE`, «check my bio» → `promo`.
3. `test_junk_emoji_only` — только эмодзи/хэштеги → `empty`.
4. `test_junk_bot_call` — «@grok is this true?» → `bot_call`.
5. `test_junk_low_followers_and_likes` — 10 подписчиков → `low_author`; 2 лайка → `low_likes`.
6. `test_junk_lang` — `lang = "ja"` при `langs = ["en"]` → `lang`; `lang = None` и английский текст → `None`.
7. `test_junk_self_and_parent_author` — автор = владелец или автор поста → `self`.

Отбор и исход:
8. `test_select_top_by_likes_not_api_order` — 100 ответов в случайном порядке → 20 лучших по лайкам; копипаста считается один раз.
9. `test_contrast_only_from_same_window` — слабые ответы позже 120 мин в контраст не идут; из окна идут ≤ 5 с лайками ≤ 10% медианы топа.
10. `test_sibling_tier` — share 0.06 → hit; 3-е место с 25 лайками → hit; share 0.02 → above; иначе normal; контраст → flop.

Хранение и отделение голоса:
11. `test_harvest_stores_inspiration_with_parent` — после прогона: исходный пост `reference`/`reply_harvest`, 20 ответов `reply`/`inspiration` с `parent_source_id`, `meta.harvest`, `meta.silent`; ни одного `own`.
12. `test_harvest_uses_own_parent_if_exists` — пост владельца уже есть с `stance = own` → новый родитель не создаётся, `own` не меняется.
13. `test_harvest_never_in_voice_examples` — после сбора и индексации `build_context_pack` для ответа не содержит ни одного куска из материалов сбора в `examples`.
14. `test_harvest_does_not_touch_passport` — активный паспорт и его версия до и после сбора одинаковы; задачи перестройки паспорта нет.
15. `test_guess_kind_does_not_override_harvest` — тегер угадал «own_post» → у материала сбора остаётся `inspiration`.

Дубли, лимиты, тишина:
16. `test_same_post_enqueued_once` — две ссылки на один пост и «🔁 Ещё варианты» → одна задача (ключ идемпотентности).
17. `test_existing_reply_gets_signal_not_new_source` — ответ с таким `external_id` уже есть → нового `sources` нет, есть новый сигнал `public_metrics`.
18. `test_harvest_runs_now_takes_what_exists` — пост 10 мин назад, под ним 4 ответа по 0–1 лайку → задача с `run_at = now`, взяты все 4, прошедшие текстовый фильтр (порога лайков нет), контраста нет; 0 ответов → задача `done`, материалов нет.
18a. `test_recheck_scheduled_for_fresh_post` — пост моложе суток → после сбора стоит `brain.harvest_recheck` через 24 ч; пост старше суток → перепроверки нет.
18b. `test_recheck_updates_and_tops_up` — при перепроверке у собранных ответов новый сигнал и `brain.detect revise`, добраны новые до `per_post`, дублей нет, сообщений нет.
19. `test_daily_cap_reschedules` — потолок 500 достигнут → задача перенесена на сутки, материалов не добавлено, сообщений нет.
20. `test_fxembed_down_no_error_to_owner` — `XUnavailable` 3 раза → задача `failed`, владельцу ничего, режим ответов работает.
21. `test_harvest_silent_no_messages` — полный прогон сбора → разбора → извлечения: notifier получает только «🧠 Выучила…» (или ничего), нет «Это что?», «🧩 Разбор», сводки пачки.
22. `test_toggle_off_no_harvest` — `reply_learning = false` → задача не ставится.
23. `test_harvest_kind_in_fast_lane` — `brain.harvest_replies` не в `SLOW_KINDS`.

Пакет писателя:
24. `test_reply_task_pack_gets_reply_knowledge` — активное знание со скоупом `format = reply, lang = en` попадает в пакет задачи из режима ответов; знание `format = thread` — нет.

---

## R4. Выбор владельца и «👎 Не то» — M

**Модель:** Sonnet, medium (понятная логика поверх готовых `Decisions`, сбора R3 и `pg_trgm`).
**Ветка:** `feat/reply-learning-04-pick`.
**Файлы:** `src/xcopilot/brain/harvest.py` (`match_owner_reply(account_id, post_id, owner_reply) -> PickResult`), `src/xcopilot/db/repos/drafts.py` (черновики режима по `view.post_id`), `src/xcopilot/bot/keyboards.py` («👎 Не то» `rpl:nope:<run_id>` в клавиатуре конца показа), `src/xcopilot/bot/routers/replies.py` (обработчик: 3 × `Decisions.reject(off_topic, comment="reply_nope")`, затем генерация с подсказкой), `src/xcopilot/writer/…` (поле подсказки «прошлые не подошли» в задаче; промпт писателя не трогать, только данные задачи), метрики `reply_pick_matched_rate`, `reply_nope_rate` (`metrics/definitions/`).

**Тесты (до кода):**
1. `test_owner_reply_matches_variant_unchanged` — ответ владельца = вариант 2 → черновик 2 `approved`, `meta.via = harvest_match`; 1 и 3 без решения по `fit`.
2. `test_owner_reply_edited_variant` — similarity 0.7, текст правлен → `Decisions.edit`, сильная правка даёт `fit −0.5`.
3. `test_owner_reply_no_match` — similarity < 0.6 у всех → решений нет; ответ всё равно сохранён как `own`/`reply`.
4. `test_owner_reply_saved_as_own_reply` — `kind = reply`, `stance = own`, `parent_source_id` = пост; это единственный `own` от сбора.
5. `test_no_owner_reply_nothing_marked` — ответа владельца нет → черновики не меняются.
6. `test_nope_rejects_three_and_regenerates` — «👎 Не то» → 3 `rejected` (`off_topic`), новая генерация с подсказкой и первыми строками прошлых; занятость режима (п. 7 ADR 0032) соблюдается.
7. `test_nope_then_match_skipped` — отвергнутые «Не то» черновики автосверка не одобряет.
8. `test_nope_twice_idempotent` — повторное нажатие → «уже учтено», без второй генерации.

---

## Сводка этапов

| Этап | Размер | Модель / усилие | Зависит от |
|---|---|---|---|
| R1 поля клиента | XS | Sonnet low / DeepSeek | fxembed-v2 |
| R2 контексты и миграция 0013 | M | Opus medium | — |
| R3 сбор | L | Opus medium (+ DeepSeek на чистые функции) | R2, fxembed-v2 |
| R4 выбор и «Не то» | M | Sonnet medium | R3 |

## R5 (по метрике). «↩️ Ответить» с отметкой выбора — S

Владелец согласен (18.11). Запускать, если через 2 недели после R4 `reply_pick_matched_rate < 50%`.
**Модель:** Sonnet, low.
**Что:** URL-кнопку «↩️ Ответить в X» заменить колбэком `rpl:pick:<draft_id>`. Колбэк делает `Decisions.approve` и правит сообщение: вместо кнопки ставит URL-кнопку «Открыть в X» с тем же intent. Автосверка остаётся: она ловит правки текста в X.
**Тесты (до кода):**
1. `test_pick_approves_and_shows_url` — нажатие → черновик `approved`, у сообщения URL-кнопка intent.
2. `test_pick_twice_idempotent` — повторное нажатие → тот же URL, решения не дублируются.
3. `test_match_after_pick_records_edit` — выбран вариант 2, в X текст правлен → `Decisions.edit` поверх выбора.
