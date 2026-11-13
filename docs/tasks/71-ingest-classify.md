## Файлы
- src/xcopilot/ingest/classify.py

## Новые
- (нет)

## Контекст
- tests/unit/ingest/test_classify.py
- src/xcopilot/x/urls.py

## Задача
Реализуй `classify(message: Incoming) -> InputKind` в `src/xcopilot/ingest/classify.py`. Остальное в файле не меняй.

Правила по порядку:
1. `message.file_name` не None → `InputKind.FILE`.
2. `text = (message.text or "").strip()`; пусто → `InputKind.EMPTY`.
3. `tokens = text.split()`. Если каждый токен — ссылка на пост (`parse_post_url(token)` из `xcopilot.x.urls` не None **и** `_POST_URL.fullmatch(token)`, где `_POST_URL = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/\w{1,15}/status(?:es)?/\d+(?:[/?#]\S*)?", re.I)` — объяви на уровне модуля) → `InputKind.LINKS`.
4. Если токен один и (он начинается с `@` или совпадает с `re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/@?\w{1,15}/?", re.I)` через fullmatch) и `parse_handle(token)` не None → `InputKind.HANDLE`.
5. Иначе → `InputKind.TEXT`.

Должен пройти `test_classify_text_url_handle_file`.

## Коммит
feat(ingest): classify — тип входа
