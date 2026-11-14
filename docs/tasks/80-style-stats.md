## Файлы
- src/xcopilot/style/stats.py

## Новые
- (нет)

## Контекст
- tests/unit/style/test_stats.py
- src/xcopilot/style/passport.py

## Задача
Класс `StyleStats` и сигнатуры функций не меняй. Реализуй две функции.

1. `compute_style_stats(texts: Sequence[str]) -> StyleStats`:
   - пустая последовательность → `StyleStats()` (все нули);
   - `n` = число текстов; доли считаются от `n`, округления нет;
   - `lowercase_share`: доля текстов, где есть хотя бы одна буква (`str.isalpha`) и нет ни одной заглавной (`str.isupper()` по символу); тексты без букв в числитель не входят;
   - `emoji_share`: доля текстов, где есть эмодзи. Эмодзи = символ с кодом в диапазонах 0x1F300–0x1FAFF, 0x1F1E6–0x1F1FF, 0x2600–0x27BF, 0x2B00–0x2BFF, 0x2300–0x23FF, плюс 0x2764 и 0xFE0F; регулярка, скомпилированная один раз на уровне модуля;
   - `hashtag_share`: доля текстов с хэштегом, регулярка `(?<!\w)#\w+`;
   - `link_share`: доля текстов со ссылкой, регулярка `https?://|\bt\.co/`;
   - `median_len`: `int(statistics.median(len(t) for t in texts))` (длина в символах `len`; для чётного числа — среднее двух средних, округлённое вниз до int);
   - `p90_len`: 90-й процентиль длин: отсортировать длины, взять элемент с индексом `min(n - 1, math.ceil(0.9 * n) - 1)`.
2. `derive_hard_rules(stats: StyleStats) -> HardRules`:
   - `stats.n == 0` → `HardRules()`;
   - `casing = "lower"` если `lowercase_share >= 0.9`, иначе `"any"`;
   - `emoji`, `hashtags`, `links` из `emoji_share`, `hashtag_share`, `link_share` через одну общую функцию `_level(share)`: `share <= 0.1` → `"none"`; `share <= 0.4` → `"rare"`; иначе `"free"` (сравнение с допуском `1e-9`: `share <= 0.1 + 1e-9`);
   - `max_len`: если `p90_len <= 280` → 280; иначе `min(25000, ceil(p90_len * 1.1 / 10) * 10)`;
   - `banned_phrases` пустой список.
3. Не добавляй зависимостей. Должны пройти все тесты из `tests/unit/style/test_stats.py`.

## Коммит
feat(style): статистика стиля и жёсткие правила из неё
