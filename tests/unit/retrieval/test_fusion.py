"""RRF и MMR: чистая логика на детерминированных примерах."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from xcopilot.retrieval.search import Hit, mmr, rrf


def mk(n: int, score: float = 0.0, vec: tuple[float, ...] | None = None, text: str = "") -> Hit:
    return Hit(
        chunk_id=uuid.UUID(int=n),
        text=text or f"chunk {n}",
        kind="own_post",
        source_id=uuid.UUID(int=1000 + n),
        tags=(),
        lang="ru",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        score=score,
        vec=vec,
    )


def test_rrf_merges_and_ranks() -> None:
    a, b, c, d = mk(1), mk(2), mk(3), mk(4)
    merged = rrf([[a, b, c], [b, d, a]], k=60)
    # B: 1/62+1/61, A: 1/61+1/63, D: 1/62, C: 1/63
    assert [h.chunk_id for h in merged] == [b.chunk_id, a.chunk_id, d.chunk_id, c.chunk_id]
    expected = {b.chunk_id: 1 / 62 + 1 / 61, a.chunk_id: 1 / 61 + 1 / 63}
    for h in merged[:2]:
        assert abs(h.score - expected[h.chunk_id]) < 1e-12


def test_rrf_ties_break_by_chunk_id() -> None:
    a, b = mk(1), mk(2)
    # у обоих один и тот же вклад (разные списки, одинаковые ранги)
    assert [h.chunk_id for h in rrf([[b], [a]])] == [a.chunk_id, b.chunk_id]
    assert [h.chunk_id for h in rrf([[a], [b]])] == [a.chunk_id, b.chunk_id]


def test_rrf_keeps_vector_from_any_list() -> None:
    with_vec = mk(1, vec=(1.0, 0.0))
    without = mk(1)
    merged = rrf([[without], [with_vec]])
    assert merged[0].vec == (1.0, 0.0)


def test_rrf_empty_inputs() -> None:
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_mmr_removes_near_duplicates() -> None:
    top = mk(1, score=1.0, vec=(1.0, 0.0, 0.0))
    twin = mk(2, score=0.95, vec=(0.999, 0.04, 0.0))
    other = mk(3, score=0.6, vec=(0.0, 1.0, 0.0))
    third = mk(4, score=0.5, vec=(0.0, 0.0, 1.0))
    picked = mmr([top, twin, other, third], lambda_=0.7, k=2)
    assert [h.chunk_id for h in picked] == [top.chunk_id, other.chunk_id]


def test_mmr_first_pick_is_most_relevant_and_k_is_respected() -> None:
    hits = [mk(i, score=1.0 - i / 10, vec=(float(i), 1.0)) for i in range(1, 6)]
    picked = mmr(hits, k=3)
    assert len(picked) == 3
    assert picked[0].chunk_id == hits[0].chunk_id
    assert mmr(hits, k=99) and len(mmr(hits, k=99)) == 5
    assert mmr([], k=3) == []


def test_mmr_without_vectors_falls_back_to_text_overlap() -> None:
    a = mk(1, score=1.0, text="купил биткоин на просадке")
    dup = mk(2, score=0.9, text="купил биткоин на просадке сегодня")
    diff = mk(3, score=0.5, text="рецепт борща без свёклы")
    picked = mmr([a, dup, diff], lambda_=0.7, k=2)
    assert [h.chunk_id for h in picked] == [a.chunk_id, diff.chunk_id]


def test_mmr_is_deterministic_regardless_of_input_order() -> None:
    hits = [mk(i, score=0.5, vec=(1.0, 0.0)) for i in range(1, 6)]
    forward = [h.chunk_id for h in mmr(hits, k=3)]
    backward = [h.chunk_id for h in mmr(list(reversed(hits)), k=3)]
    assert forward == backward
