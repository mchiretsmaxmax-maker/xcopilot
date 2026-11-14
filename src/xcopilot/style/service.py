"""Версии паспорта: черновик → активный → архив, откат и правки владельца.

Правка владельца (`patch_hard_rule`, `add_banned_phrase`, `remove_rule`) создаёт новый
черновик `created_by=user`; вступает в силу после `activate`. Основа правки — «рабочая»
версия: непринятый пользовательский черновик новее активной (правки копятся), иначе активная,
иначе последняя.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.repos import PassportsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit
from xcopilot.style.errors import NothingToRollback, StyleError, UnknownRule
from xcopilot.style.extractor import StyleExtractor
from xcopilot.style.passport import RULE_NAMES, HardRules, StylePassport

PassportRow = Any  # строка style_passports (модели вне пакета db импортировать нельзя)
DEFAULTS = HardRules()
CASING_TEXT = {"lower": "только строчные", "any": "любой"}
LEVEL_TEXT = {"none": "нет", "rare": "редко", "free": "свободно"}


class PassportVersion(BaseModel):
    id: UUID
    version: int
    status: str
    created_by: str
    created_at: datetime
    passport: StylePassport


def _view(row: PassportRow) -> PassportVersion:
    return PassportVersion(
        id=row.id,
        version=row.version,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
        passport=StylePassport.model_validate(row.body),
    )


def _rules_column(rules: HardRules) -> list[dict[str, Any]]:
    """Колонка hard_rules: проверяемые кодом правила списком {name, value}."""
    items: list[dict[str, Any]] = [
        {"name": name, "value": getattr(rules, name)} for name in RULE_NAMES
    ]
    items += [{"name": "banned_phrase", "value": p} for p in rules.banned_phrases]
    return items


def _source_refs(passport: StylePassport) -> list[str]:
    refs = [c for e in passport.evidence for c in e.chunk_ids] + [
        x.chunk_id for x in passport.examples
    ]
    return list(dict.fromkeys(refs))


def list_rules(passport: StylePassport) -> list[tuple[str, str]]:
    """Пронумерованные правила для показа и «убери правило N»: (имя, текст), по порядку.

    В список входят только правила, отличные от значений по умолчанию, и запреты фраз.
    """
    rules = passport.hard_rules
    items: list[tuple[str, str]] = []
    if rules.casing != DEFAULTS.casing:
        items.append(("casing", f"Регистр: {CASING_TEXT[rules.casing]}"))
    for name, title in (("emoji", "Эмодзи"), ("hashtags", "Хэштеги"), ("links", "Ссылки")):
        value = getattr(rules, name)
        if value != getattr(DEFAULTS, name):
            items.append((name, f"{title}: {LEVEL_TEXT[value]}"))
    if rules.max_len != DEFAULTS.max_len:
        items.append(("max_len", f"Длина: до {rules.max_len} знаков"))
    items += [(f"banned:{p}", f"Запрет: «{p}»") for p in rules.banned_phrases]
    return items


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def describe_change(old: StylePassport | None, new: StylePassport) -> str:
    """«+2 запрета, −1 правило» — чем новая версия отличается от старой."""
    if old is None:
        return "первая версия"
    if old == new:
        return "без изменений"
    old_bans = {p.casefold() for p in old.hard_rules.banned_phrases}
    new_bans = {p.casefold() for p in new.hard_rules.banned_phrases}
    old_toggles = {(n, t) for n, t in list_rules(old) if not n.startswith("banned:")}
    new_toggles = {(n, t) for n, t in list_rules(new) if not n.startswith("banned:")}
    parts: list[str] = []
    for sign, count, forms in (
        ("+", len(new_bans - old_bans), ("запрет", "запрета", "запретов")),
        ("−", len(old_bans - new_bans), ("запрет", "запрета", "запретов")),
        ("+", len(new_toggles - old_toggles), ("правило", "правила", "правил")),
        ("−", len(old_toggles - new_toggles), ("правило", "правила", "правил")),
    ):
        if count:
            parts.append(f"{sign}{count} {_plural(count, *forms)}")
    return ", ".join(parts) or "обновил описание стиля"


class StyleService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], extractor: StyleExtractor | None = None
    ) -> None:
        self._uow = uow_factory
        self._extractor = extractor

    # --- чтение ---------------------------------------------------------------------------

    async def get(self, account_id: UUID, version: int) -> PassportVersion | None:
        async with self._uow() as uow:
            row = await PassportsRepo(uow.session, account_id).get_by_version(version)
            return _view(row) if row else None

    async def get_active(self, account_id: UUID) -> PassportVersion | None:
        async with self._uow() as uow:
            row = await PassportsRepo(uow.session, account_id).get_active()
            return _view(row) if row else None

    async def list_versions(self, account_id: UUID) -> list[PassportVersion]:
        async with self._uow() as uow:
            return [_view(r) for r in await PassportsRepo(uow.session, account_id).list_versions()]

    async def get_working(self, account_id: UUID) -> PassportVersion | None:
        """Рабочая версия: основа следующей правки владельца (см. описание модуля)."""
        async with self._uow() as uow:
            row = await self._working_row(PassportsRepo(uow.session, account_id))
            return _view(row) if row else None

    @staticmethod
    async def _working_row(repo: PassportsRepo) -> PassportRow | None:
        latest = await repo.latest()
        if latest is not None and latest.status == "draft" and latest.created_by == "user":
            return latest
        return await repo.get_active() or latest

    # --- версии ---------------------------------------------------------------------------

    async def _create(
        self,
        session: DbSession,
        account_id: UUID,
        passport: StylePassport,
        *,
        created_by: str,
        user_id: UUID | None,
        source_refs: list[str] | None = None,
    ) -> PassportRow:
        repo = PassportsRepo(session, account_id)
        version = await repo.next_version()
        row = await repo.add(
            version=version,
            status="draft",
            body=passport.model_dump(mode="json"),
            hard_rules=_rules_column(passport.hard_rules),
            source_refs=_source_refs(passport) if source_refs is None else source_refs,
            created_by=created_by,
        )
        await emit(
            session,
            account_id=account_id,
            type=EventType.PASSPORT_CREATED,
            actor="user" if created_by == "user" else "system",
            user_id=user_id,
            entity=("style_passport", row.id),
            props={"version": version},
        )
        return row

    async def create_draft_version(
        self,
        account_id: UUID,
        passport: StylePassport,
        *,
        created_by: str = "llm",
        user_id: UUID | None = None,
        source_refs: list[str] | None = None,
    ) -> PassportVersion:
        """Новая версия-черновик; активной она станет только после `activate`."""
        async with self._uow() as uow:
            row = await self._create(
                uow.session,
                account_id,
                passport,
                created_by=created_by,
                user_id=user_id,
                source_refs=source_refs,
            )
            return _view(row)

    async def _activate(
        self, session: DbSession, account_id: UUID, row: PassportRow, user_id: UUID | None
    ) -> PassportRow:
        if row.status == "active":
            return row
        activated = await PassportsRepo(session, account_id).activate(row.id)
        await emit(
            session,
            account_id=account_id,
            type=EventType.PASSPORT_ACTIVATED,
            actor="user" if user_id else "system",
            user_id=user_id,
            entity=("style_passport", row.id),
            props={"version": row.version},
        )
        return activated

    async def activate(
        self, account_id: UUID, version: int, *, user_id: UUID | None = None
    ) -> PassportVersion:
        """Сделать версию активной; прежняя активная уходит в архив."""
        async with self._uow() as uow:
            row = await PassportsRepo(uow.session, account_id).get_by_version(version)
            if row is None:
                raise NotFound(f"паспорт v{version} не найден")
            return _view(await self._activate(uow.session, account_id, row, user_id))

    async def rollback(self, account_id: UUID, *, user_id: UUID | None = None) -> PassportVersion:
        """Вернуть ближайшую более раннюю архивную версию; NothingToRollback, если её нет."""
        async with self._uow() as uow:
            repo = PassportsRepo(uow.session, account_id)
            active = await repo.get_active()
            target = await repo.archived_below(active.version) if active else None
            if target is None:
                raise NothingToRollback("нет прежней версии для отката")
            return _view(await self._activate(uow.session, account_id, target, user_id))

    # --- правки владельца -----------------------------------------------------------------

    async def _edit(
        self,
        account_id: UUID,
        user_id: UUID,
        change: Callable[[StylePassport], StylePassport | None],
    ) -> PassportVersion | None:
        async with self._uow() as uow:
            repo = PassportsRepo(uow.session, account_id)
            base_row = await self._working_row(repo)
            base = StylePassport.model_validate(base_row.body) if base_row else StylePassport()
            edited = change(base)
            if edited is None:
                return None
            row = await self._create(
                uow.session, account_id, edited, created_by="user", user_id=user_id
            )
            return _view(row)

    async def patch_hard_rule(
        self, account_id: UUID, name: str, value: Any, *, user_id: UUID
    ) -> PassportVersion:
        """Переключатель жёсткого правила → новая версия владельца (черновик)."""
        if name not in RULE_NAMES:
            raise UnknownRule(f"нет жёсткого правила «{name}»")

        def change(base: StylePassport) -> StylePassport:
            data = {**base.hard_rules.model_dump(), name: value}
            return base.model_copy(update={"hard_rules": HardRules.model_validate(data)})

        created = await self._edit(account_id, user_id, change)
        assert created is not None
        return created

    async def add_banned_phrase(
        self, account_id: UUID, phrase: str, *, user_id: UUID
    ) -> PassportVersion | None:
        """Добавить запрет фразы; регистр не важен; повтор → None (версия не создаётся)."""
        clean = phrase.strip()
        if not clean:
            raise ValueError("пустой запрет")

        def change(base: StylePassport) -> StylePassport | None:
            current = base.hard_rules.banned_phrases
            if clean.casefold() in {p.casefold() for p in current}:
                return None
            rules = base.hard_rules.model_copy(update={"banned_phrases": [*current, clean]})
            return base.model_copy(update={"hard_rules": rules})

        return await self._edit(account_id, user_id, change)

    async def remove_rule(self, account_id: UUID, number: int, *, user_id: UUID) -> PassportVersion:
        """«Убери правило N» (нумерация list_rules): переключатель → по умолчанию, запрет → снят."""

        def change(base: StylePassport) -> StylePassport:
            rules = list_rules(base)
            if not 1 <= number <= len(rules):
                raise UnknownRule(f"нет правила {number}")
            name = rules[number - 1][0]
            hard = base.hard_rules
            if name.startswith("banned:"):
                phrase = name.removeprefix("banned:")
                hard = hard.model_copy(
                    update={"banned_phrases": [p for p in hard.banned_phrases if p != phrase]}
                )
            else:
                hard = hard.model_copy(update={name: getattr(DEFAULTS, name)})
            return base.model_copy(update={"hard_rules": hard})

        created = await self._edit(account_id, user_id, change)
        assert created is not None
        return created

    async def rebuild(self, account_id: UUID, *, user_id: UUID | None = None) -> PassportVersion:
        """Пересобрать паспорт моделью по собственным постам → новая версия-черновик."""
        if self._extractor is None:
            raise StyleError("извлечение паспорта не подключено")
        passport = await self._extractor.extract(account_id)
        return await self.create_draft_version(
            account_id, passport, created_by="llm", user_id=user_id
        )
