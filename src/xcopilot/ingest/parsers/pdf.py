"""Парсер: pdf."""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from xcopilot.ingest.errors import NoTextLayer, ParseError
from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes) -> list[ParsedDoc]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) == 0:
            raise ParseError("Не удалось прочитать PDF. Пришли другой файл или текст.")
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        raise ParseError("Не удалось прочитать PDF. Пришли другой файл или текст.") from exc

    if all(not page for page in pages):
        raise NoTextLayer("В файле нет текстового слоя. Пришли текст или другой файл.")

    return [ParsedDoc(text="\n\n".join(p for p in pages if p), meta={"pages": len(pages)})]
