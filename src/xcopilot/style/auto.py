"""Автопересборка паспорта по новым своим постам без AI-шаблонов (feat/clean-voice).

Владелец пометил материал «это моё» (приём своего поста, «Всё моё», кнопка карточки) →
`request_auto_rebuild` ставит задачу `style.auto_rebuild` (одна в очереди на аккаунт, с
паузой `delay`, чтобы пачка постов дала одну пересборку). Задача:

- новые = чистые свои источники, которых нет в `built_from` последней собранной версии
  (по номеру: после отката откаченная версия остаётся точкой отсчёта — её посты не
  считаются новыми, повтора пересборки нет);
- паспорта ещё нет — нужно ≥ `min_first` чистых постов, иначе ≥ `min_new` новых;
- всего чистых своих меньше `min_first` — паспорт не трогается, владельцу «мало чистых
  постов: N» (решение главного чата 19.11);
- с прошлой автосборки (`built_at`) прошло меньше `min_interval` — задача переставляется на
  срок, ничего не теряется;
- пересборка через `StyleService.rebuild_merged` (ручное владельца цело), новая версия сразу
  активна, владельцу одно сообщение с кнопкой отката. Без вопросов.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from xcopilot.bot import keyboards, texts
from xcopilot.clock import Clock
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.style.clean_voice import voice_filter
from xcopilot.style.errors import NoOwnPosts
from xcopilot.style.service import StyleService

AUTO_REBUILD_KIND = "style.auto_rebuild"
CONFIG = Path(__file__).resolve().parents[3] / "config" / "passport.toml"


class Notifier(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


@dataclass(frozen=True)
class AutoConfig:
    min_new: int = 5
    min_first: int = 10
    min_interval: timedelta = timedelta(hours=12)
    delay: timedelta = timedelta(minutes=2)


@dataclass(frozen=True)
class AutoResult:
    """Итог: built / below_threshold / too_few_clean / too_soon / no_posts; new — сколько новых."""

    reason: str
    new: int = 0
    version: int | None = None


def load_auto_config(path: Path = CONFIG) -> AutoConfig:
    """Пороги из `config/passport.toml` [auto_rebuild]; нет файла — значения по умолчанию."""
    if not path.exists():
        return AutoConfig()
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("auto_rebuild", {})
    base = AutoConfig()
    return AutoConfig(
        min_new=int(raw.get("min_new", base.min_new)),
        min_first=int(raw.get("min_first", base.min_first)),
        min_interval=timedelta(hours=float(raw.get("min_interval_hours", 12))),
        delay=timedelta(minutes=float(raw.get("delay_minutes", 2))),
    )


async def request_auto_rebuild(
    session: DbSession, account_id: UUID, *, now: datetime, config: AutoConfig | None = None
) -> UUID | None:
    """Поставить проверку автопересборки; уже стоит в очереди — None (одна на аккаунт)."""
    cfg = config or load_auto_config()
    repo = JobsRepo(session)
    if await repo.has_queued(account_id, AUTO_REBUILD_KIND):
        return None
    return await repo.enqueue(
        account_id=account_id,
        kind=AUTO_REBUILD_KIND,
        payload={},
        run_at=now + cfg.delay,
        max_attempts=3,
        idempotency_key=None,
    )


async def run_auto_rebuild(
    *,
    account_id: UUID,
    service: StyleService,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    notifier: Notifier | None,
    config: AutoConfig | None = None,
) -> AutoResult:
    """Проверить пороги и, если пора, пересобрать и активировать паспорт (см. модуль)."""
    cfg = config or load_auto_config()
    now = clock.now()
    async with uow_factory() as uow:
        clean = {str(s) for s in (await voice_filter(uow.session, account_id)).clean_ids}
    versions = await service.list_versions(account_id)
    built = next((v.passport for v in reversed(versions) if v.passport.built_from), None)
    new = len(clean - set(built.built_from)) if built else len(clean)
    if new < (cfg.min_new if versions else cfg.min_first) or new == 0:
        return AutoResult("below_threshold", new)
    if len(clean) < cfg.min_first:  # решение главного чата 19.11: мало — паспорт не трогаем
        if notifier is not None:
            await notifier.notify(account_id, texts.passport_too_few_clean(len(clean)))
        return AutoResult("too_few_clean", new)
    anchor = next((v.passport.built_at for v in reversed(versions) if v.passport.built_at), None)
    if anchor is not None and now < anchor + cfg.min_interval:
        async with uow_factory() as uow:
            repo = JobsRepo(uow.session)
            if not await repo.has_queued(account_id, AUTO_REBUILD_KIND):
                await repo.enqueue(
                    account_id=account_id,
                    kind=AUTO_REBUILD_KIND,
                    payload={},
                    run_at=anchor + cfg.min_interval,
                    max_attempts=3,
                    idempotency_key=None,
                )
        return AutoResult("too_soon", new)
    try:
        rebuilt = await service.rebuild_merged(account_id, built_at=now)
    except NoOwnPosts:
        return AutoResult("no_posts", new)
    active = await service.activate(account_id, rebuilt.version.version)
    if notifier is not None:
        await notifier.notify(
            account_id,
            texts.passport_auto_rebuilt(new, active.version),
            keyboard=keyboards.passport_auto_rebuilt(),
        )
    return AutoResult("built", new, active.version)


async def handle_auto_rebuild(
    job: Any,
    *,
    service: StyleService | None,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    notifier: Notifier | None,
) -> AutoResult:
    """Задача `style.auto_rebuild` (payload пустой)."""
    if service is None:
        return AutoResult("no_service")
    return await run_auto_rebuild(
        account_id=job.account_id,
        service=service,
        uow_factory=uow_factory,
        clock=clock,
        notifier=notifier,
    )
