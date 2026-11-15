"""Автосверка публикаций со своей лентой (ТЗ, этап 12, задача 9).

Одобренный или исправленный черновик ищется среди своих постов в течение 48 часов после
решения: сходство от 0.85 — публикация найдена сама (`matched_by = auto`), от 0.6 до 0.85 —
вопрос владельцу, после 48 часов без совпадения — `unconfirmed`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from rapidfuzz import fuzz

from xcopilot.clock import Clock
from xcopilot.db.repos import DraftsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.review import AWAITING, AwaitingDraft, ReviewQueries
from xcopilot.db.uow import UnitOfWork
from xcopilot.review.publish import record_publication
from xcopilot.review.settings import ReviewSettings
from xcopilot.x.models import PostRef, XPost

SKEW = timedelta(minutes=10)  # часы X и наши могут расходиться


@dataclass(frozen=True)
class Match:
    draft_id: UUID
    outcome: Literal["auto", "ask", "unconfirmed"]
    x_post_id: str | None = None
    url: str | None = None
    score: float = 0.0


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def similarity(a: str, b: str) -> float:
    """Сходство текстов 0..1 (rapidfuzz, без регистра и лишних пробелов)."""
    return float(fuzz.ratio(_norm(a), _norm(b))) / 100.0


class PublicationMatcher:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings or ReviewSettings()

    async def match_publications(self, account_id: UUID, own_posts: Sequence[XPost]) -> list[Match]:
        """Сверить ждущие публикации черновики со своими постами; auto — сразу публикация.

        Пост берётся, только если вышел не раньше решения и не позже конца окна. Каждый пост —
        не больше чем к одному черновику (лучшее сходство выигрывает). Черновик без совпадения
        после окна — `unconfirmed`.
        """
        t = self._settings
        now = self._clock.now()
        async with self._uow() as uow:
            q = ReviewQueries(uow.session, account_id)
            waiting = await q.awaiting_publication()
            taken = await q.published_post_ids()
        posts = [p for p in own_posts if p.id not in taken]
        pairs: list[tuple[float, AwaitingDraft, XPost]] = []
        for draft in waiting:
            until = draft.decided_at + t.match_window
            for post in posts:
                at = post.created_at
                if at is not None and not (draft.decided_at - SKEW <= at <= until):
                    continue
                score = similarity(draft.text, post.text)
                if score >= t.ask_match:
                    pairs.append((score, draft, post))
        pairs.sort(key=lambda p: -p[0])
        used_drafts: set[UUID] = set()
        used_posts: set[str] = set()
        out: list[Match] = []
        for score, draft, post in pairs:
            if draft.draft_id in used_drafts or post.id in used_posts:
                continue
            used_drafts.add(draft.draft_id)
            used_posts.add(post.id)
            ref = PostRef(post.id, post.author_handle)
            if score >= t.auto_match:
                async with self._uow() as uow:
                    await record_publication(
                        uow.session,
                        account_id,
                        draft.draft_id,
                        post=ref,
                        final_text=post.text,
                        published_at=post.created_at or now,
                        matched_by="auto",
                        score=score,
                    )
                out.append(Match(draft.draft_id, "auto", post.id, ref.url, score))
            else:
                out.append(Match(draft.draft_id, "ask", post.id, ref.url, score))
        for draft in waiting:
            if draft.draft_id in used_drafts or now <= draft.decided_at + t.match_window:
                continue
            async with self._uow() as uow:
                drafts = DraftsRepo(uow.session, account_id)
                row = await drafts.require(draft.draft_id)
                if row.status in AWAITING:
                    await drafts.set_status(draft.draft_id, DraftStatus.UNCONFIRMED)
            out.append(Match(draft.draft_id, "unconfirmed"))
        return out
