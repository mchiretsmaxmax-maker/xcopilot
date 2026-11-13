## Файлы
- src/xcopilot/ingest/parsers/telegram_export.py
- src/xcopilot/ingest/parsers/xpost.py
- src/xcopilot/ingest/parsers/__init__.py

## Новые
- (нет)

## Контекст
- src/xcopilot/ingest/parsers/base.py
- src/xcopilot/ingest/parsers/text.py
- src/xcopilot/ingest/parsers/pdf.py
- src/xcopilot/ingest/parsers/docx.py
- src/xcopilot/ingest/errors.py
- src/xcopilot/x/models.py
- tests/unit/ingest/test_parsers.py
- tests/samples.py

## Задача
Сигнатуры не меняй.

1. `telegram_export.parse(data: bytes, owner_tg_id: int | None = None) -> list[ParsedDoc]`:
   - `json.loads(data)`; ошибка (`ValueError`, `UnicodeDecodeError`) или корень не dict, или нет списка `messages` → `ParseError("Это не экспорт Telegram (нужен result.json).")`.
   - id владельца: `owner_tg_id`; если None — `root.get("personal_information", {}).get("user_id")`; если и его нет → `ParseError("Не понял, где твои сообщения в экспорте.")`.
   - брать только элементы с `type == "message"` и `from_id == f"user{owner}"`.
   - текст элемента: поле `text` — строка или список (строки и dict с ключом `text`); склеить части без разделителя; `.strip()`; пустые пропускать.
   - нет ни одного сообщения владельца → `ParseError("В экспорте нет твоих сообщений.")`.
   - результат: `[ParsedDoc(text="\n\n".join(тексты), meta={"messages": число, "chat": root.get("name")})]`.
   Тест проверяет `meta["messages"]` и `text`; ключ `chat` допустим.
   Внимание: в тесте `test_telegram_export_keeps_only_owner_messages` ожидается `meta["messages"] == 2` — значит ключ chat не мешает.
2. `xpost.parse(data: XPost) -> list[ParsedDoc]`: `[ParsedDoc(text=data.text, meta={"post_id": data.id, "author_handle": data.author_handle, "truncated": data.possibly_truncated})]`.
3. `parsers/__init__.py`: `parse_file(name, data, *, owner_tg_id=None)`: расширение `Path(name).suffix.lower()`; `.txt`/`.md` → `text.parse(data)`; `.pdf` → `pdf.parse(data)`; `.docx` → `docx.parse(data)`; `.json` → `telegram_export.parse(data, owner_tg_id)`; иное → `ParseError("Такой формат не беру: пришли txt, md, pdf, docx или result.json из Telegram.")`. Импорты подмодулей внутри пакета: `from xcopilot.ingest.parsers import docx, pdf, telegram_export, text`.

Должны пройти все тесты `tests/unit/ingest/test_parsers.py`.

## Коммит
feat(ingest): парсеры экспорта Telegram, поста X и выбор по расширению
