## Файлы
- src/xcopilot/ingest/parsers/text.py
- src/xcopilot/ingest/parsers/pdf.py
- src/xcopilot/ingest/parsers/docx.py

## Новые
- (нет)

## Контекст
- src/xcopilot/ingest/parsers/base.py
- src/xcopilot/ingest/errors.py
- tests/unit/ingest/test_parsers.py
- tests/samples.py

## Задача
Реализуй `parse(data: bytes) -> list[ParsedDoc]` в трёх файлах. Сигнатуры не меняй. Каждый возвращает ровно один `ParsedDoc`.

1. `parsers/text.py` (txt и md): декодировать `data.decode("utf-8")`; при `UnicodeDecodeError` — `data.decode("cp1251")`; при ошибке и там — `data.decode("utf-8", errors="replace")`. Убрать BOM `﻿` в начале. Вернуть `[ParsedDoc(text=декодированное, meta={})]`. Текст не чистить.
2. `parsers/pdf.py`: `import io`, `from pypdf import PdfReader`, `from pypdf.errors import PdfReadError`.
   - `reader = PdfReader(io.BytesIO(data))`; любые исключения при открытии и чтении страниц (`PdfReadError`, `ValueError`, `KeyError`, `TypeError`, `OSError`, `IndexError`) → `raise ParseError("Не удалось прочитать PDF. Пришли другой файл или текст.") from exc`.
   - `pages = [(p.extract_text() or "").strip() for p in reader.pages]`.
   - если `len(reader.pages) == 0` → тот же `ParseError`.
   - если все страницы пустые → `raise NoTextLayer("В файле нет текстового слоя. Пришли текст или другой файл.")`.
   - иначе `ParsedDoc(text="\n\n".join(p for p in pages if p), meta={"pages": len(pages)})`.
3. `parsers/docx.py`: `import io`, `import docx as python_docx` (пакет python-docx; модуль называется так же, как наш файл, поэтому в начале файла обязательно `from __future__ import annotations` и импорт `import docx as python_docx` работает, т.к. абсолютный). Если mypy ругается на отсутствие типов — добавь `# type: ignore[import-untyped]` только на эту строку.
   - `document = python_docx.Document(io.BytesIO(data))`; исключения (`Exception`, кроме `MemoryError`) → `raise ParseError("Не удалось прочитать DOCX. Пришли другой файл или текст.") from exc`.
   - абзацы: `[p.text.strip() for p in document.paragraphs]`, пустые выбросить, склеить через `"\n\n"`.
   - пусто → `raise ParseError("В документе нет текста.")`.
   - `ParsedDoc(text=..., meta={"paragraphs": число непустых})`.

Должны пройти `test_pdf_text_extracted`, `test_scanned_pdf_reports_no_text_without_crash`, `test_docx_parsed`.

## Коммит
feat(ingest): парсеры txt/md, pdf, docx
