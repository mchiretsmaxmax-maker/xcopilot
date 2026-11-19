#!/usr/bin/env python3
"""Разовый «Чистый голос» (feat/clean-voice, решения владельца 19.11).

1. Чужие посты, лежащие как свои (stance = own, автор из списка и url не владельца — цитата
   владельца остаётся своей), → `inspiration`: приёмы
   остаются, из голоса уходят. Тот же механизм, что кнопка «Всё вдохновение»: у разобранных
   ставится brain.detect с revise (доказательства отзываются, веса пересчитываются воркером).
2. Отчёт фильтра AI-шаблонов (детектор ai_slop) по своим постам: сколько прошло, кто и чем
   отсеян. Отсеянные остаются своими — для цифр и приёмов.
3. Пересборка паспорта из прошедших (`merge_rebuild`: «Мои правила», owner_set, правила
   форматов и свои примеры владельца сохраняются), новая версия активна, старая — в истории.

Идемпотентен: повтор ничего не меняет (чужих own уже нет; паспорт уже собран из тех же постов).
`--dry-run` только читает БД и печатает, что изменил бы; модель не зовёт.

Запуск (после бэкапа, из корня репозитория):
    uv run python scripts/oneoff/clean_voice.py --account prima_labs --dry-run
    uv run python scripts/oneoff/clean_voice.py --account prima_labs
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from xcopilot.brain.batch import set_stance_for_sources
from xcopilot.clock import Clock, SystemClock
from xcopilot.db.repos import AccountsRepo, SourcesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.auto import load_auto_config
from xcopilot.style.clean_voice import split_voice, voice_filter
from xcopilot.style.diff import diff_lines
from xcopilot.style.errors import NoOwnPosts
from xcopilot.style.parse_md import OWNER_CHUNK
from xcopilot.style.service import StyleService

FOREIGN_HANDLES = (
    "viktoroddy",
    "simonschubert",
    "tonysimons",
    "EliBenSasson",
    "alpha_pls",
    "ilyabatishchev",
    "RoundtableSpace",
    "zodchiii",
    "claudeai",
    "poteto",
)
TARGET_STANCE = "inspiration"


@dataclass
class Report:
    restanced: list[UUID] = field(default_factory=list)
    revise_jobs: int = 0
    clean: int = 0
    dirty: list[tuple[str, list[str]]] = field(default_factory=list)
    built: int | None = None


def _owner_url(url: str | None, own_handle: str) -> bool:
    """Ссылка на пост владельца: x.com/<own_handle>/… (регистр не важен)."""
    return bool(url and own_handle and f"/{own_handle}/" in url.lower())


def _short(text: str, n: int = 70) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"


async def run(
    uow_factory: Callable[[], UnitOfWork],
    *,
    account: str,
    handles: Sequence[str],
    dry_run: bool,
    style: StyleService | None,
    clock: Clock,
    out: Callable[[str], None] = print,
    min_first: int | None = None,
) -> Report:
    report = Report()
    wanted = {h.lower() for h in handles}
    out(f"=== «Чистый голос» для @{account}{' — DRY-RUN, ничего не меняю' if dry_run else ''} ===")
    async with uow_factory() as uow:
        acc = await AccountsRepo(uow.session).get_by_handle(account)
        if acc is None:
            raise SystemExit(f"аккаунт @{account} не найден")
        account_id, own_handle = acc.id, (acc.handle or "").lower()
        authored = await SourcesRepo(uow.session, account_id).own_with_authors()

    # --- 1. чужие own → inspiration -------------------------------------------------------
    listed = [s for s in authored if (s.author_handle or "").lower() in wanted]
    # чужой хэндл, но url — пост владельца (его цитата): это моё, не переносится (19.11)
    quotes = [s for s in listed if _owner_url(s.url, own_handle)]
    foreign = [s for s in listed if not _owner_url(s.url, own_handle)]
    others = [s for s in authored if (s.author_handle or "").lower() not in wanted | {own_handle}]
    out(f"\n1) Чужие посты как свои → {TARGET_STANCE}: {len(foreign)}")
    for s in foreign:
        out(f"   @{s.author_handle}  {s.url or s.id}  (разбор: {s.analysis_status})")
    for s in quotes:
        out(f"   = оставляю своим (url владельца, цитата): @{s.author_handle}  {s.url}")
    if others:
        out(f"   ! другие чужие own вне списка (не трогаю): {len(others)}")
        for s in others:
            out(f"     @{s.author_handle}  {s.url or s.id}")
    report.restanced = [s.id for s in foreign]
    analyzed = sum(1 for s in foreign if s.analysis_status == "analyzed")
    if foreign and not dry_run:
        async with uow_factory() as uow:
            report.revise_jobs = await set_stance_for_sources(
                uow.session, account_id, report.restanced, TARGET_STANCE, now=clock.now()
            )
        out(
            f"   готово; задач пересмотра доказательств (brain.detect revise): {report.revise_jobs}"
        )
    elif foreign:
        out(f"   поставил бы задач пересмотра доказательств (brain.detect revise): {analyzed}")

    # --- 2. фильтр AI-шаблонов ------------------------------------------------------------
    async with uow_factory() as uow:
        vf = await voice_filter(uow.session, account_id)
    skip = set(report.restanced)
    vf = split_voice([d for d in vf.clean + [d for d, _ in vf.dirty] if d.source_id not in skip])
    report.clean = len(vf.clean)
    report.dirty = [(str(d.source_id), [h.code for h in hits]) for d, hits in vf.dirty]
    total = len(vf.clean) + len(vf.dirty)
    mine = [
        d
        for d in vf.clean + [d for d, _ in vf.dirty]
        if own_handle and own_handle in (f"{d.author_handle or ''} {d.url or ''}".lower())
    ]
    mine_clean = sum(1 for d in mine if d.source_id in vf.clean_ids)
    out(
        f"\n2) Фильтр AI-шаблонов: своих постов {total}, прошли {len(vf.clean)}, "
        f"отсеяны {len(vf.dirty)}"
    )
    out(
        f"   из них постов @{own_handle}: {len(mine)}, прошли {mine_clean}, "
        f"отсеяны {len(mine) - mine_clean}"
    )
    for doc, hits in vf.dirty:
        found = "; ".join(f"правило ai_slop/{h.code}: «{_short(h.fragment, 40)}»" for h in hits)
        out(f"   ✗ {doc.url or doc.source_id}  «{_short(doc.text)}»\n     {found}")

    # --- 3. пересборка паспорта -----------------------------------------------------------
    out("\n3) Паспорт")
    if style is None:
        out("   пересборка не подключена (нет сервиса паспорта)")
        return report
    versions = await style.list_versions(account_id)
    active = await style.get_active(account_id)
    if active is not None:
        p = active.passport
        own_examples = sum(1 for e in p.examples if e.chunk_id == OWNER_CHUNK)
        out(
            f"   активный v{active.version} ({active.created_by}); сохраняется: «Мои правила» "
            f"{len(p.owner_rules.splitlines())} строк, owner_set {p.owner_set}, правила "
            f"форматов {sorted(p.format_rules)}, свои примеры {own_examples}"
        )
    clean = {str(d.source_id) for d in vf.clean}
    built = next((v.passport for v in reversed(versions) if v.passport.built_from), None)
    if built is not None and set(built.built_from) == clean:
        out("   уже собран из тех же постов — пересборка не нужна")
        return report
    need = load_auto_config().min_first if min_first is None else min_first
    if len(clean) < need:  # решение главного чата 19.11: мало чистых — паспорт не трогаем
        out(f"   мало чистых постов: {len(clean)} (нужно ≥ {need}) — паспорт не тронут")
        return report
    if dry_run:
        out(f"   пересобрал бы из {len(clean)} прошедших постов (claude -p), активировал бы")
        return report
    try:
        rebuilt = await style.rebuild_merged(account_id, built_at=clock.now())
    except NoOwnPosts:
        out("   нет своих постов без AI-шаблонов — паспорт не тронут")
        return report
    done = await style.activate(account_id, rebuilt.version.version)
    report.built = done.version
    base = active.passport if active else None
    out(f"   собран и активирован v{done.version} из {len(clean)} постов; откат — /style → ↩️")
    for line in diff_lines(base, done.passport):
        out(f"   • {line}")
    for prop in rebuilt.proposals:
        out(f"   ? модель предлагает (не применено): {prop}")
    return report


async def _amain(args: argparse.Namespace) -> int:
    from xcopilot.config import load_settings
    from xcopilot.db.engine import create_engine, session_factory
    from xcopilot.llm.factory import build_gateway
    from xcopilot.style.extractor import StyleExtractor

    settings = load_settings()
    engine = create_engine(settings)
    sf = session_factory(engine)
    clock = SystemClock()

    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    if args.dry_run:
        style = StyleService(uow)  # только чтение версий; модель не нужна
    else:
        style = StyleService(uow, StyleExtractor(uow, build_gateway(settings, uow, clock)))
    try:
        await run(
            uow,
            account=args.account,
            handles=args.handles.split(",") if args.handles else FOREIGN_HANDLES,
            dry_run=args.dry_run,
            style=style,
            clock=clock,
        )
    finally:
        await engine.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--account", default="prima_labs", help="хэндл аккаунта без @")
    parser.add_argument(
        "--handles", default="", help="чужие авторы через запятую (по умолчанию 10)"
    )
    parser.add_argument("--dry-run", action="store_true", help="только показать, ничего не менять")
    return asyncio.run(_amain(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
