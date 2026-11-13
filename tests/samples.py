"""Образцы файлов для тестов ингеста: PDF (с текстом и «скан»), DOCX, экспорт Telegram."""

from __future__ import annotations

import io
import json
from typing import Any


def _esc(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """Минимальный PDF: страница на элемент списка; пустая строка — страница без текста (скан)."""
    n = len(pages)
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
            + f"] /Count {n} >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        if text:
            body = " ".join(f"({_esc(line)}) Tj T*" for line in text.split("\n"))
            ops = f"BT /F1 11 Tf 72 740 Td 13 TL {body} ET"
        else:
            ops = "0 0 1 rg 72 72 400 600 re f"  # картинка-заглушка без текста
        stream = ops.encode("latin-1")
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % num + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    )
    return out.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_tg_export(owner_tg_id: int, messages: list[tuple[int, Any]]) -> bytes:
    """result.json личного чата: (tg_id автора, text) — text строка или список сущностей."""
    items: list[dict[str, Any]] = [
        {"id": 1, "type": "service", "actor_id": f"user{owner_tg_id}", "action": "phone_call"}
    ]
    for i, (author, text) in enumerate(messages, start=2):
        items.append(
            {
                "id": i,
                "type": "message",
                "date": "2026-11-01T10:00:00",
                "from": "Владелец" if author == owner_tg_id else "Собеседник",
                "from_id": f"user{author}",
                "text": text,
            }
        )
    return json.dumps(
        {"name": "Собеседник", "type": "personal_chat", "id": 42, "messages": items},
        ensure_ascii=False,
    ).encode()


def english_page(n: int, words: int = 220) -> str:
    """Страница английского текста (латиница — Helvetica в PDF без шрифтов)."""
    base = [
        "Rollups",
        "settle",
        "on",
        "Ethereum",
        "and",
        "post",
        "data",
        "to",
        "blobs",
        "while",
        "sequencers",
        "order",
        "transactions",
        "for",
        "users",
        "who",
        "want",
        "cheap",
        "and",
        "fast",
        "confirmations",
    ]
    text = " ".join(f"{base[i % len(base)]}" for i in range(words))
    return f"Page {n}. {text}."


_SYL = ("ка", "ро", "ми", "ту", "ле", "зо", "ба", "ни", "ве", "лу", "до", "ши", "ре", "фа")


def unique_text(seed: int, words: int = 12) -> str:
    """Псевдослучайная фраза: разные seed дают тексты, далёкие по триграммам."""
    import random

    rnd = random.Random(seed)
    out = ["".join(rnd.choice(_SYL) for _ in range(rnd.randint(2, 4))) for _ in range(words)]
    return " ".join(out).capitalize() + "."
