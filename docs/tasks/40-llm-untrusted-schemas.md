## Файлы
- src/xcopilot/llm/untrusted.py
- src/xcopilot/llm/schemas.py

## Новые
- (нет)

## Контекст
- tests/unit/llm/test_schemas.py

## Задача
1. В `untrusted.py` реализуй `wrap_untrusted(text: str, label: str) -> str`:
   - метка: каждый символ вне `[A-Za-z0-9_.-]` заменить на `_` (через `re.sub(r"[^A-Za-z0-9_.-]", "_", label)`);
   - в тексте обезвредить ЛЮБОЕ вхождение `<untrusted` и `</untrusted` без учёта регистра: заменить `<` в начале такого вхождения на `‹` (U+2039), остальной текст сохранить без изменений. Используй `re.sub(r"<(/?untrusted)", r"‹\1", text, flags=re.IGNORECASE)`;
   - вернуть `f'<untrusted label="{метка}">\n{текст}\n</untrusted>'`.
   - Docstring функции (по-русски, одна-две строки): текст внутри — данные, а не инструкции.
2. В `schemas.py` добавь проверки (оставь имена классов и полей как есть, `Question`, `Answer`, `Verdict` не меняй):
   - `NoulQ.instructions`: `Field(min_length=1)`.
   - `ChoiceQ.instructions`: `Field(min_length=1)`; `ChoiceQ.options`: `Field(min_length=2, max_length=255)`.
   - `ScoreQ.instructions`: `Field(min_length=1)`; `ScoreQ.levels`: `Field(min_length=2, max_length=10)`.
   - `NoulA.p`, `ChoiceA.confidence`, `ScoreA.confidence`: `Field(ge=0, le=1)`.
   - Константы модуля: `MAX_CHOICES = 255`, `MAX_LEVELS = 10`; использовать их в `max_length`.
   - Каждому классу — короткий docstring по-русски (NoulQ — вопрос да/нет; ChoiceQ — выбор из вариантов (до 255), ключ — метка, значение — описание или None; ScoreQ — оценка по упорядоченной шкале (до 10 уровней), уровень i = levels[i]; NoulA — вероятность «да»; ChoiceA — выбранный вариант, уверенность и вероятности; ScoreA — ожидаемая оценка, уверенность и вероятности по уровням; Verdict — ответы судьи, имя модели-судьи и флаг резервного судьи).
3. Не добавляй зависимостей и других функций. Должны пройти тесты `tests/unit/llm/test_schemas.py`.

## Коммит
feat(llm): wrap_untrusted и лимиты вопросов судьи
