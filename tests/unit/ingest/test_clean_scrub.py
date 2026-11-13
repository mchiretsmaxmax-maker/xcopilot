from xcopilot.ingest.clean import normalize, strip_tracking
from xcopilot.ingest.scrub import CARD_MASK, EMAIL_MASK, PHONE_MASK, scrub_pii


def test_normalize_spaces_and_invisible() -> None:
    raw = "  Привет,​   мир!\t\r\n\r\n\r\n\r\nВторой  абзац   \n"
    assert normalize(raw) == "Привет, мир!\n\nВторой абзац"
    assert normalize("é") == "é"


def test_tracking_params_removed() -> None:
    assert strip_tracking("https://x.com/a/status/1?s=20&t=abc") == "https://x.com/a/status/1"
    assert (
        strip_tracking("https://site.io/p?id=5&utm_source=tw&fbclid=zz#h")
        == "https://site.io/p?id=5#h"
    )
    assert normalize("см. https://site.io/p?utm_medium=x тут") == "см. https://site.io/p тут"


def test_scrub_pii_masks() -> None:
    res = scrub_pii(
        "Пиши на ivan.petrov@mail.ru или +7 (999) 123-45-67, 8 999 765 43 21. "
        "Карта 4276 3800 1234 5679 (не 1234 5678 9012 3456). @alice держит $ETH, "
        "рынок 1 000 000 000, блок 21000000."
    )
    assert "ivan.petrov" not in res.text and EMAIL_MASK in res.text
    assert "123-45-67" not in res.text and "765 43 21" not in res.text
    assert res.text.count(PHONE_MASK) == 2
    assert CARD_MASK in res.text and "4276" not in res.text
    assert "1234 5678 9012 3456" in res.text  # не проходит Луна — не карта
    assert "@alice" in res.text and "$ETH" in res.text
    assert "1 000 000 000" in res.text and "21000000" in res.text
    assert (res.emails, res.phones, res.cards, res.total) == (1, 2, 1, 4)
