import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from xcopilot.ingest.chunker import chunk, chunk_spans, count_tokens


def _paragraphs(n: int, words: int) -> str:
    return "\n\n".join(
        " ".join(f"слово{p}_{w}," for w in range(words)) + " конец." for p in range(n)
    )


def test_chunker_respects_max_tokens_and_overlap() -> None:
    assert count_tokens("Привет, мир!") == 4
    assert chunk("Короткий текст.") == ["Короткий текст."]
    assert chunk("   \n ") == []

    text = _paragraphs(30, 30)  # ~60 токенов в абзаце, ~1800 всего
    spans = chunk_spans(text, max_tokens=400, overlap=50)
    pieces = chunk(text, max_tokens=400, overlap=50)
    assert len(pieces) >= 4
    assert pieces == [text[s:e] for s, e in spans]
    for piece in pieces:
        assert count_tokens(piece) <= 400
    for (s1, e1), (s2, _) in itertools.pairwise(spans):
        assert s1 < s2 < e1  # перекрытие есть
        assert 0 < count_tokens(text[s2:e1]) <= 50
    # режем по абзацам: кусок (кроме последнего) кончается на конце абзаца
    for s, e in spans[:-1]:
        assert text[s:e].rstrip().endswith("конец.")


def test_post_chunk_single_paragraph_long_falls_back_to_tokens() -> None:
    text = " ".join(f"w{i}" for i in range(1000))
    pieces = chunk(text, max_tokens=100, overlap=10)
    assert all(count_tokens(p) <= 100 for p in pieces)
    assert len(pieces) >= 10


@settings(max_examples=200, deadline=None)
@given(
    text=st.text(alphabet=st.sampled_from(list("ab ,.\n\tя1")), max_size=400),
    max_tokens=st.integers(min_value=2, max_value=40),
    data=st.data(),
)
def test_chunker_loses_no_text(text: str, max_tokens: int, data: st.DataObject) -> None:
    overlap = data.draw(st.integers(min_value=0, max_value=max_tokens - 1))
    spans = chunk_spans(text, max_tokens=max_tokens, overlap=overlap)
    if not text.strip():
        assert spans == []
        return
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    rebuilt = text[spans[0][0] : spans[0][1]]
    prev_end = spans[0][1]
    for s, e in spans[1:]:
        assert s <= prev_end < e  # без дыр, каждый кусок продвигает конец
        rebuilt += text[prev_end:e]
        prev_end = e
    assert rebuilt == text
    for s, e in spans:
        assert count_tokens(text[s:e]) <= max_tokens
