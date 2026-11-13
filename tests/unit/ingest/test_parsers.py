import json

import pytest

from tests.samples import make_docx, make_pdf, make_tg_export
from xcopilot.ingest.errors import NoTextLayer, ParseError
from xcopilot.ingest.parsers import docx, parse_file, pdf, telegram_export, text, xpost
from xcopilot.x.models import XPost


def test_pdf_text_extracted() -> None:
    data = make_pdf(["Rollups are cheap\nSecond line", "Blobs changed everything"])
    docs = pdf.parse(data)
    assert len(docs) == 1
    assert "Rollups are cheap" in docs[0].text
    assert "Blobs changed everything" in docs[0].text
    assert docs[0].meta["pages"] == 2


def test_scanned_pdf_reports_no_text_without_crash() -> None:
    with pytest.raises(NoTextLayer) as err:
        pdf.parse(make_pdf(["", ""]))
    assert err.value.user_message == "В файле нет текстового слоя. Пришли текст или другой файл."
    with pytest.raises(ParseError):
        pdf.parse(b"%PDF-1.4 broken garbage")


def test_docx_parsed() -> None:
    data = make_docx(["Первый абзац про L2.", "", "Второй абзац про блобы."])
    docs = docx.parse(data)
    assert len(docs) == 1
    assert docs[0].text == "Первый абзац про L2.\n\nВторой абзац про блобы."
    with pytest.raises(ParseError):
        docx.parse(b"not a zip")


def test_telegram_export_keeps_only_owner_messages() -> None:
    data = make_tg_export(
        100,
        [
            (100, "Мой ответ про L2"),
            (555, "Чужое сообщение"),
            (100, ["Жирно: ", {"type": "bold", "text": "блобы"}, " дешевле"]),
            (100, ""),
        ],
    )
    docs = telegram_export.parse(data, owner_tg_id=100)
    assert len(docs) == 1
    assert docs[0].text == "Мой ответ про L2\n\nЖирно: блобы дешевле"
    assert docs[0].meta["messages"] == 2
    assert "Чужое" not in docs[0].text
    with pytest.raises(ParseError):
        telegram_export.parse(data, owner_tg_id=777)  # нет сообщений владельца
    with pytest.raises(ParseError):
        telegram_export.parse(json.dumps({"x": 1}).encode(), owner_tg_id=100)


def test_text_parser_and_dispatch() -> None:
    assert text.parse("# Заголовок\n\nтекст".encode())[0].text == "# Заголовок\n\nтекст"
    assert text.parse("тест".encode("cp1251"))[0].text == "тест"
    assert parse_file("a.md", b"hello")[0].text == "hello"
    assert parse_file("A.PDF", make_pdf(["Hi there"]))[0].meta["pages"] == 1
    assert parse_file("r.json", make_tg_export(1, [(1, "ok")]), owner_tg_id=1)[0].text == "ok"
    with pytest.raises(ParseError):
        parse_file("x.exe", b"MZ")


def test_xpost_parser() -> None:
    post = XPost(
        id="9", text="gm", author_handle="alice", created_at=None, source="api",
        possibly_truncated=True,
    )  # fmt: skip
    (doc,) = xpost.parse(post)
    assert doc.text == "gm"
    assert doc.meta == {"post_id": "9", "author_handle": "alice", "truncated": True}
