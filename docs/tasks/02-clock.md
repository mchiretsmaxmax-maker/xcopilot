## Файлы
- src/xcopilot/clock.py

## Новые
- (нет)

## Контекст
- tests/unit/test_clock.py
- tests/fakes/clock.py

## Задача
В `src/xcopilot/clock.py` (сейчас там `Clock` Protocol и `SystemClock` с `NotImplementedError`):
1. Оставь `class Clock(Protocol)` с методом `def now(self) -> datetime: ...` как есть.
2. Реализуй `SystemClock.now(self) -> datetime`: вернуть `datetime.now(UTC)` (aware, UTC). `from datetime import UTC, datetime`.
3. Импорты упорядочи по стандарту (`datetime` раньше `typing`). Docstring модуля — «Интерфейс времени: в прикладном коде время берётся только через Clock.» Docstring у `SystemClock` — «Настоящее системное время (UTC).»
4. Должны пройти тесты из `tests/unit/test_clock.py`. Больше ничего не добавляй.

## Коммит
feat: Clock и SystemClock
