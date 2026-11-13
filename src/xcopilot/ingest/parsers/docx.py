"""Парсер: docx."""

from __future__ import annotations

import io

import docx as python_docx

from xcopilot.ingest.errors import ParseError
from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes) -> list[ParsedDoc]:
    try:
        document = python_docx.Document(io.BytesIO(data))
    except MemoryError:
        raise
    except Exception as exc:
        raise ParseError("Не удалось прочитать DOCX. Пришли другой файл или текст.") from exc

    paragraphs = [p.text.strip() for p in document.paragraphs]
    non_empty = [p for p in paragraphs if p]

    if not non_empty:
        raise ParseError("В документе нет текста.")

    return [ParsedDoc(text="\n\n".join(non_empty), meta={"paragraphs": len(non_empty)})]
