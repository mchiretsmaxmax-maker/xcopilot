"""Команды администратора для runbook (этап 15, задача 14): блокировка автора и переразбор.

    docker compose exec app python -m xcopilot.ops.admin block-author <аккаунт> <автор>
    docker compose exec app python -m xcopilot.ops.admin reanalyze <аккаунт> [--all]

`block-author` — `brain.evidence.revoke(author_id=…, reason="author_blocked")`: доказательства
автора отозваны, веса пересчитаны, новые материалы автора не учатся. `reanalyze` ставит
`brain.analyze` материалам аккаунта (по умолчанию — упавшим и ждущим, с `--all` — всем); после
разбора сам ставится `brain.detect`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import text

from xcopilot.brain.evidence import revoke
from xcopilot.brain.jobs import ANALYZE_KIND, BrainDeps
from xcopilot.clock import Clock, SystemClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue

UowFactory = Callable[[], UnitOfWork]


class AdminError(RuntimeError):
    """Нет аккаунта или автора — понятное сообщение вместо трассировки."""


async def _account(uow_factory: UowFactory, handle: str) -> UUID:
    async with uow_factory() as uow:
        acc = await AccountsRepo(uow.session).get_by_handle(handle.lstrip("@"))
    if acc is None:
        raise AdminError(f"нет аккаунта @{handle.lstrip('@')}")
    return acc.id


async def block_author(uow_factory: UowFactory, clock: Clock, account: str, author: str) -> int:
    """Заблокировать автора в аккаунте; вернуть число отозванных доказательств."""
    account_id = await _account(uow_factory, account)
    deps = BrainDeps(uow_factory=uow_factory, clock=clock)
    async with uow_factory() as uow:
        row = (
            await uow.session.execute(
                text("SELECT id FROM authors WHERE account_id = :a AND lower(handle) = :h"),
                {"a": account_id, "h": author.lstrip("@").lower()},
            )
        ).scalar_one_or_none()
        if row is None:
            raise AdminError(f"нет автора @{author.lstrip('@')} в аккаунте @{account}")
        return await revoke(
            deps.ctx(uow.session, account_id), author_id=row, reason="author_blocked"
        )


async def reanalyze(
    uow_factory: UowFactory, clock: Clock, account: str, *, everything: bool = False
) -> int:
    """Поставить brain.analyze материалам аккаунта; вернуть число задач."""
    account_id = await _account(uow_factory, account)
    statuses = ("analyzed", "failed", "pending", "skipped") if everything else ("failed", "pending")
    async with uow_factory() as uow:
        ids = (
            (
                await uow.session.execute(
                    text(
                        "SELECT id FROM sources WHERE account_id = :a AND removed_at IS NULL"
                        " AND analysis_status = ANY(:s) ORDER BY created_at"
                    ),
                    {"a": account_id, "s": list(statuses)},
                )
            )
            .scalars()
            .all()
        )
    queue = JobQueue(uow_factory, clock)
    stamp = clock.now().strftime("%Y%m%dT%H%M")
    for sid in ids:
        await queue.enqueue(
            ANALYZE_KIND,
            {"source_id": str(sid)},
            account_id=account_id,
            idempotency_key=f"reanalyze:{sid}:{stamp}",
        )
    return len(ids)


async def _run(args: argparse.Namespace) -> str:  # pragma: no cover - CLI
    from xcopilot.config import load_settings
    from xcopilot.db.engine import create_engine, session_factory

    engine = create_engine(load_settings())
    sf = session_factory(engine)

    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    try:
        if args.command == "block-author":
            n = await block_author(uow, SystemClock(), args.account, args.author)
            return f"автор @{args.author.lstrip('@')} заблокирован, отозвано доказательств: {n}"
        n = await reanalyze(uow, SystemClock(), args.account, everything=args.all)
        return f"поставлено на разбор: {n}"
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(prog="python -m xcopilot.ops.admin")
    sub = parser.add_subparsers(dest="command", required=True)
    block = sub.add_parser("block-author", help="заблокировать автора материалов")
    block.add_argument("account")
    block.add_argument("author")
    again = sub.add_parser("reanalyze", help="поставить материалы на повторный разбор")
    again.add_argument("account")
    again.add_argument("--all", action="store_true", help="все материалы, а не только упавшие")
    try:
        print(asyncio.run(_run(parser.parse_args(argv))))
    except AdminError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
