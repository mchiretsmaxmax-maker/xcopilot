from xcopilot.ingest.classify import Incoming, InputKind, classify


def test_classify_text_url_handle_file() -> None:
    assert classify(Incoming(text="Сегодня запустил ноду и понял, что L2 — это про UX.")) == (
        InputKind.TEXT
    )
    assert classify(Incoming(text="https://x.com/alice/status/123")) == InputKind.LINKS
    assert (
        classify(Incoming(text="https://x.com/alice/status/1\n twitter.com/bob/status/2?s=20"))
        == InputKind.LINKS
    )
    # ссылка внутри обычного текста — это текст поста
    assert classify(Incoming(text="Читайте тред https://x.com/a/status/1 — огонь")) == (
        InputKind.TEXT
    )
    assert classify(Incoming(text="@vitalik")) == InputKind.HANDLE
    assert classify(Incoming(text="https://x.com/vitalik")) == InputKind.HANDLE
    assert classify(Incoming(text="привет")) == InputKind.TEXT  # одно слово — не хэндл
    assert classify(Incoming(file_name="book.pdf", file_size=10)) == InputKind.FILE
    assert classify(Incoming(text="подпись", file_name="a.txt", file_size=3)) == InputKind.FILE
    assert classify(Incoming(text="   ")) == InputKind.EMPTY
    assert classify(Incoming()) == InputKind.EMPTY
