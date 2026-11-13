## Файлы
- src/xcopilot/ingest/clean.py

## Новые
- (нет)

## Контекст
- tests/unit/ingest/test_clean_scrub.py

## Задача
Реализуй в `src/xcopilot/ingest/clean.py` две функции (сигнатуры не меняй, только стандартная библиотека):

1. `strip_tracking(url: str) -> str` — убрать служебные параметры из query ссылки:
   - через `urllib.parse.urlsplit` / `parse_qsl(keep_blank_values=True)` / `urlencode` / `urlunsplit`;
   - удалять ключи: начинающиеся на `utm_`, а также `fbclid`, `gclid`, `yclid`, `igshid`, `mc_cid`, `mc_eid`, `ref_src`, `ref_url`;
   - для хоста `x.com`, `twitter.com` (и с префиксом `www.` / `mobile.`) дополнительно удалять `s` и `t`;
   - порядок остальных параметров и fragment сохранить; если query опустел — без `?`.
2. `normalize(text: str) -> str`:
   - `unicodedata.normalize("NFC", text)`;
   - удалить символы `​ ‌ ‍ ⁠ ﻿`;
   - `\r\n` и `\r` → `\n`;
   - в каждой строке: подряд идущие пробелы/табы (`[ \t ]+`) → один пробел; срезать пробелы в конце строки и в начале строки;
   - 3 и более переводов строк подряд → ровно два (`\n\n`);
   - каждую ссылку `https?://\S+` заменить на `strip_tracking(ссылка)`;
   - `.strip()` всего результата.
3. Должны пройти `test_normalize_spaces_and_invisible` и `test_tracking_params_removed`.

## Коммит
feat(ingest): normalize и strip_tracking
