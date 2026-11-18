# Кассеты FxEmbed API v2 (api.fxtwitter.com/2/…)

Настоящие ответы, записаны живыми запросами 18.11.2026 (User-Agent `xcopilot/1.0`, без ключа).
Файл — тело ответа как есть (JSON); код ответа указан ниже. В тестах подставляются через respx.

| Файл | Запрос | Код |
|---|---|---|
| status_note.json | `/2/status/2026084506822730185` (XDevelopers, длинный пост `is_note_tweet`) | 200 |
| status_media.json | `/2/status/1585841941203537920` (ответ с фото) | 200 |
| status_reply.json | `/2/status/2026110348722618597` (ответ на XDevelopers) | 200 |
| status_404.json | `/2/status/1999999999999999999` | 404 |
| thread_naval_mid.json | `/2/thread/1002103627387813888` (4-я часть треда naval, 34 части) | 200 |
| thread_single.json | `/2/thread/2026084506822730185` (одиночный пост) | 200 |
| conversation_p1.json | `/2/conversation/2026084506822730185?ranking_mode=likes` | 200 |
| conversation_p2.json | то же + `cursor` из p1 | 200 |
| conversation_self_thread.json | `/2/conversation/1002103360646823936` (первые 70 из 93 ответов; среди них части треда автора) | 200 |
| quotes_p1.json | `/2/status/2026084506822730185/quotes` | 200 |
| quotes_p2.json | то же + `cursor` из p1 | 200 |
| profile_nasa.json | `/2/profile/NASA` | 200 |
