## Файлы
- src/xcopilot/x/intents.py

## Новые
- (нет)

## Контекст
- tests/unit/x/test_intents.py

## Задача
Реализуй функции в `x/intents.py` (константы и сигнатуры не меняй; только стандартная библиотека: `re`, `unicodedata`, `urllib.parse`).

1. `weighted_len(text: str) -> int` — правила twitter-text v3:
   - сначала `text = unicodedata.normalize("NFC", text)`;
   - каждая ссылка `https?://\S+` (regex `_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)`; конечные знаки
     препинания `.,!?:;)»"'` в конце совпадения не входят в ссылку и считаются как обычные символы) весит `URL_WEIGHT` (23);
   - остальной текст режется на «единицы»: эмодзи-последовательность — одна единица весом 2. Последовательность
     начинается с символа-эмодзи (кодовая точка в диапазонах U+1F000–U+1FAFF, U+2600–U+27BF, U+2B00–U+2BFF,
     U+1F1E6–U+1F1FF) и поглощает следующие за ним: U+FE0F, U+FE0E, модификаторы оттенка U+1F3FB–U+1F3FF,
     U+20E3, теги U+E0020–U+E007F, а также пары «U+200D + следующий символ-эмодзи» (ZWJ-склейка);
   - любой другой символ весит 1, если его кодовая точка в одном из диапазонов: U+0000–U+10FF, U+2000–U+200D,
     U+2010–U+201F, U+2032–U+2037; иначе 2.
   - Вернуть сумму весов.
2. `_encode(params: list[tuple[str, str]]) -> str` (приватная): `INTENT_URL + "?" + "&".join(f"{k}={quote(v, safe='')}")`
   где `quote` — `urllib.parse.quote`. Пробел должен стать `%20` (не `+`).
3. `build_post_intent(text)`: если `weighted_len(text) > MAX_WEIGHTED_LEN` → `None`; иначе `_encode([("text", text)])`.
4. `build_reply_intent(text, in_reply_to_id)`: то же ограничение; параметры `[("text", text), ("in_reply_to", in_reply_to_id)]`.
5. `build_quote_intent(text, quoted_url)`: `full = f"{text} {quoted_url}"`; если `weighted_len(full) > 280` → `None`;
   иначе `_encode([("text", full)])`.
6. Должны пройти все тесты из `tests/unit/x/test_intents.py`.

## Коммит
feat(x): Web Intent и weighted_len по правилам X
