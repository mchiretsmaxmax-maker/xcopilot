# ADR 0031 — Чтение публичных постов по ссылке без ключа X

Статус: принято (17.11.2026). Ветка `feat/x-link-read`. Миграции нет.

## Проблема

Платного X API нет (`x_read_mode = off`). oEmbed обрезает длинные посты и иногда не отвечает, бот просил «пришли текст» — владелец считает это недопустимым.

## Решение

1. `x/public_read.py::PublicPostReader.get_post(ref)`: сначала oEmbed (текст, автор, без метрик); если текст пуст, обрезан (`…`) или oEmbed вернул ошибку — бесплатный `api.fxtwitter.com/<handle|i>/status/<id>` (полный текст, автор, метрики likes/retweets/replies).
2. Таймаут 10 с, один повтор при сетевой ошибке/5xx на каждый источник. Оба сказали «нет» → `XNotFound` (бот просит текст). Оба недоступны → `XUnavailable`. Обрезанный oEmbed при недоступном fxtwitter возвращается как есть (`possibly_truncated`).
3. `XService` принимает любой `PostFetcher` (`fetch`); `app.py` подставляет `PublicPostReader`. Лента, поиск, API-режимы не тронуты: при сбое API или бюджете `XService` и так падает назад на этот же читатель.
4. Выключатель `X_PUBLIC_READ` (по умолчанию 1); 0 — только oEmbed, как раньше.
5. Источник: `XPost.source = "fxtwitter"` (в `documents.origin` остаётся `link_oembed`), в лог — `x.public_read` с `via=oembed|fxtwitter`.
6. Приватность: наружу уходит только адрес публичного поста.

## Последствия

- Зависимость от стороннего бесплатного сервиса; его сбой — откат на oEmbed.
- fxtwitter не даёт bookmarks; в `XMetrics` они 0 (у oEmbed метрик нет — `None`).
- Опора для «режима ответов».

## Дополнение 18.11.2026 — FxEmbed API v2 (ветка `feat/fxembed-v2`)

1. Пост читается с `api.fxtwitter.com/2/status/<id>` (объект в ключе `status`, ретвиты — `reposts`; старая форма v1 с `tweet`/`retweets` по-прежнему разбирается). Порядок «oEmbed → fxtwitter» и цифры для автоисхода (ADR 0033) не менялись. `XPost` получил `lang`, `replying_to` (хэндл родителя; id — в `in_reply_to_id`), `media` (`XMedia`: тип+адрес), `is_note_tweet`.
2. Тред: `fetch_thread(ref)` (`/2/thread/<id>`) — части одного автора подряд вокруг присланной (чужие предки отброшены). `fetch_target(ref)` — пост с текстом всего треда «1/N …» (не длиннее 8000 знаков, дальше «…»); сбой → обычный `get_post`. Боту: `read_target(reader, ref)` в `bot/routers/write.py` и `replies.py`; у читателя без `fetch_target` (только oEmbed, фейки) — обычный `fetch`. `XService`, автооценка и загрузка по ссылке тред не читают.
3. `fetch_replies(post_id, limit=30)` (`/2/conversation`, `ranking_mode=likes`) и `fetch_quotes(post_id, limit=30)` (`/2/status/<id>/quotes`) — простые списки `XPost`, отсортированные по лайкам; ответы без «@хэндл» в начале, без ответов самого автора и без ответов-картинок без текста. `fetch_profile(handle)` → `XProfile`, нигде не используется.
4. Лимит сервиса 1000 запросов/мин: пауза 0,1 с между любыми запросами одного читателя, листание `cursor` не больше 5 страниц. Сбой первой страницы — `XUnavailable`/`XNotFound`, следующих — что успели собрать.
5. Тесты — на настоящих ответах API, записанных один раз (`tests/fixtures/x/fxembed/`), без сети.
