"""Извлечение паспорта: выборка примеров в пределах токен-бюджета и вызов модели `writer`."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.style_samples import OwnChunk, StyleSamplesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.ingest.chunker import count_tokens
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.style.errors import NoOwnPosts
from xcopilot.style.passport import (
    Evidence,
    Example,
    FormatShare,
    HardRules,
    Lexicon,
    Structure,
    StylePassport,
)
from xcopilot.style.stats import compute_style_stats, derive_hard_rules

DEFAULT_BUDGET_TOKENS = 6000
ROUGH_BELOW = 5
PROMPT_KEY = "style.extract.v1"


@dataclass(frozen=True)
class Sample:
    id: UUID
    text: str
    format: str
    tokens: int


class Claim(BaseModel):
    """Утверждение модели с id кусков-доказательств."""

    text: str
    evidence: list[str] = Field(default_factory=list)


class ExtractOut(BaseModel):
    """Ответ модели. Её `hard_rules` игнорируются: правила берутся из статистики."""

    summary: str = ""
    tone: str = ""
    lexicon_use: list[Claim] = Field(default_factory=list)
    lexicon_avoid: list[Claim] = Field(default_factory=list)
    structure: Structure = Field(default_factory=Structure)
    banned_phrases: list[Claim] = Field(default_factory=list)
    signature_patterns: list[Claim] = Field(default_factory=list)
    example_chunk_ids: list[str] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    hard_rules: HardRules | None = None


def format_of(tags: Sequence[str]) -> str:
    """Формат куска по метке `format:<имя>`; без метки — other."""
    for tag in tags:
        if tag.startswith("format:"):
            return tag.removeprefix("format:") or "other"
    return "other"


def pick_samples(own: Sequence[OwnChunk], budget_tokens: int) -> list[Sample]:
    """Куски по кругу из каждого формата (новые первыми), пока влезают в токен-бюджет."""
    groups: dict[str, list[Sample]] = {}
    for chunk in own:
        fmt = format_of(chunk.tags)
        groups.setdefault(fmt, []).append(
            Sample(chunk.id, chunk.text, fmt, count_tokens(chunk.text))
        )
    picked: list[Sample] = []
    left = budget_tokens
    queues = [list(items) for items in groups.values()]
    while any(queues):
        for queue in queues:
            while queue and queue[0].tokens > left:
                queue.pop(0)  # не влезает — пропускаем, пробуем следующий короче
            if queue:
                sample = queue.pop(0)
                picked.append(sample)
                left -= sample.tokens
    return picked


def _keep(claims: Sequence[Claim], valid: set[str]) -> list[tuple[str, list[str]]]:
    """Утверждения, у которых остались доказательства среди своих кусков; без дублей."""
    seen: set[str] = set()
    kept: list[tuple[str, list[str]]] = []
    for claim in claims:
        text = claim.text.strip()
        ids = list(dict.fromkeys(i for i in claim.evidence if i in valid))
        if not text or not ids or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        kept.append((text, ids))
    return kept


class StyleExtractor:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        *,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._budget = budget_tokens

    async def _own_chunks(self, account_id: UUID) -> list[OwnChunk]:
        async with self._uow() as uow:
            return await StyleSamplesRepo(uow.session, account_id).own_post_chunks()

    async def sample_examples(self, account_id: UUID, budget_tokens: int) -> list[Sample]:
        """Выборка собственных постов в пределах токен-бюджета, по форматам."""
        return pick_samples(await self._own_chunks(account_id), budget_tokens)

    async def extract(self, account_id: UUID) -> StylePassport:
        """Паспорт по собственным постам: жёсткие правила и форматы — кодом, остальное — моделью."""
        own = await self._own_chunks(account_id)
        if not own:
            raise NoOwnPosts("нет проиндексированных собственных постов")
        stats = compute_style_stats([c.text for c in own])
        rules = derive_hard_rules(stats)
        samples = pick_samples(own, self._budget)
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
        out = await self._gateway.generate(
            role=Role.WRITER,
            prompt_key=PROMPT_KEY,
            variables={
                "own_handle": account.handle if account else "",
                "count": len(samples),
                "stats": stats.model_dump_json(),
                "samples": "\n\n".join(wrap_untrusted(s.text, f"chunk_{s.id}") for s in samples),
            },
            schema=ExtractOut,
            account_id=account_id,
            purpose="style.extract",
        )
        return self._assemble(out, own, rules, stats.n)

    @staticmethod
    def _assemble(
        out: ExtractOut, own: Sequence[OwnChunk], rules: HardRules, total: int
    ) -> StylePassport:
        texts = {str(c.id): c.text for c in own}
        valid = set(texts)
        use = _keep(out.lexicon_use, valid)
        avoid = _keep(out.lexicon_avoid, valid)
        banned = _keep(out.banned_phrases, valid)
        patterns = _keep(out.signature_patterns, valid)
        evidence = [
            Evidence(claim=text, chunk_ids=ids) for text, ids in (*use, *avoid, *banned, *patterns)
        ]
        shares = Counter(format_of(c.tags) for c in own)
        return StylePassport(
            summary=out.summary,
            tone=out.tone,
            lexicon=Lexicon(use=[t for t, _ in use], avoid=[t for t, _ in avoid]),
            structure=out.structure,
            formats=[
                FormatShare(format=fmt, share=count / total) for fmt, count in shares.most_common()
            ],
            hard_rules=rules.model_copy(update={"banned_phrases": [t for t, _ in banned]}),
            signature_patterns=[t for t, _ in patterns],
            examples=[
                Example(chunk_id=i, text=texts[i])
                for i in dict.fromkeys(out.example_chunk_ids)
                if i in valid
            ],
            anti_examples=[a.strip() for a in out.anti_examples if a.strip()],
            evidence=evidence,
            rough=total < ROUGH_BELOW,
        )
