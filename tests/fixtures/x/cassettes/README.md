# Кассеты X API v2

Синтетические ответы, собранные по документации X API v2 (форматы `data` / `includes` / `meta`,
`public_metrics`, `non_public_metrics`, `note_tweet`). Ключей X на этапе 6 не было.
**Перезаписать на настоящих ключах** (см. docs/adr/0009-x-client.md). Токены в кассеты не попадают.

Формат файла: `{"request": {"method", "path", "params"}, "response": {"status", "headers", "json"}}`.
