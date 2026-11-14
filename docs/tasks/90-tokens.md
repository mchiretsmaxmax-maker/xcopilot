## Файлы
- src/xcopilot/retrieval/tokens.py

## Новые
- (нет)

## Контекст
- tests/unit/retrieval/test_tokens.py

## Задача
Полностью замени `src/xcopilot/retrieval/tokens.py`. Python 3.13, mypy --strict, docstrings на русском, без зависимостей и без сети.

Реализуй `estimate_tokens(text: str) -> int` — консервативная (завышенная) оценка числа токенов:
1. Пустая строка → 0.
2. Иначе вернуть `max(1, ceil(len(text) / 3), len(text.split()))`, где `ceil` — `math.ceil`.
   (Кириллица токенизируется хуже латиницы, поэтому знаков берём по 3 на токен; слов не может быть больше токенов.)
3. Функция должна быть монотонной при дописывании текста справа (тест `test_estimate_is_monotonic_under_concatenation`).
4. Не добавляй других функций и констант, кроме `__all__ = ["estimate_tokens"]`.

Должны пройти все тесты из `tests/unit/retrieval/test_tokens.py`.

## Коммит
feat(retrieval): estimate_tokens — консервативная оценка токенов
