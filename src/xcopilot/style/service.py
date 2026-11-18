"""Версии паспорта: черновик → активный → архив, откат и правки владельца.

Правка владельца (`patch_hard_rule`, `add_banned_phrase`, `remove_rule`) создаёт новый
черновик `created_by=user`; вступает в силу после `activate`. Основа правки — «рабочая»
версия: непринятый пользовательский черновик новее активной (правки копятся), иначе активная,
иначе последняя.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.repos import PassportsRepo
from xcopilot.db.repos.brain_owner import OwnerRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit
from xcopilot.style.diff import RULE_TITLES, diff_lines, rule_value
from xcopilot.style.errors import NothingToRollback, StyleError, UnknownRule
from xcopilot.style.extractor import StyleExtractor
from xcopilot.style.parse_md import OWNER_CHUNK, parse_md
from xcopilot.style.passport import OWNER_RULE_NAMES, RULE_NAMES, HardRules, StylePassport

PassportRow = Any  # строка style_passports (модели вне пакета db импортировать нельзя)
DEFAULTS = HardRules()
CASING_TEXT = {"lower": "только строчные", "any": "любой"}
LEVEL_TEXT = {"none": "нет", "rare": "редко", "free": "свободно"}
LABEL_MAX = 40


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
        x.chunk_id for x in passport.examples if x.chunk_id != OWNER_CHUNK
    ]
    return list(dict.fromkeys(refs))


def _owned(passport: StylePassport, names: Iterable[str]) -> StylePassport:
    """Отметить правила как заданные владельцем: пересборка их не перезапишет."""
    owner_set = list(dict.fromkeys([*passport.owner_set, *names]))
    return passport.model_copy(update={"owner_set": owner_set})


def _changed_rules(old: StylePassport, new: StylePassport) -> list[str]:
    return [n for n in OWNER_RULE_NAMES if getattr(old.hard_rules, n) != getattr(new.hard_rules, n)]


@dataclass
class FilePreview:
    """Разобранный файл владельца и его отличия от активного паспорта (ничего не создано)."""

    passport: StylePassport
    base_version: int | None
    diff: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Rebuilt:
    """Пересобранный черновик, отличия от текущего и непринятые предложения модели."""

    version: PassportVersion
    diff: list[str]
    proposals: list[str]


def merge_rebuild(
    base: StylePassport | None, fresh: StylePassport
) -> tuple[StylePassport, list[str]]:
    """Свежая сборка + ручное владельца: «Мои правила», правила из owner_set, свои примеры.

    Если модель предлагает другое значение правила владельца, оно не применяется, а
    возвращается списком предложений.
    """
    if base is None:
        return fresh.model_copy(update={"origin": "rebuild"}), []
    hard = fresh.hard_rules.model_dump()
    proposals: list[str] = []
    for name in base.owner_set:
        if name not in OWNER_RULE_NAMES:
            continue
        mine, theirs = getattr(base.hard_rules, name), hard[name]
        hard[name] = mine
        if name == "banned_phrases":
            known = {p.casefold() for p in mine}
            proposals += [f"+запрет «{p}»" for p in theirs if p.casefold() not in known]
        elif mine != theirs:
            proposals.append(
                f"{RULE_TITLES[name]}: {rule_value(name, mine)} → {rule_value(name, theirs)}"
            )
    own_examples = [e for e in base.examples if e.chunk_id == OWNER_CHUNK]
    merged = fresh.model_copy(
        update={
            "hard_rules": HardRules.model_validate(hard),
            "owner_rules": base.owner_rules,
            "owner_set": list(base.owner_set),
            "examples": [*fresh.examples, *own_examples],
            "origin": "rebuild",
            "label": "",
        }
    )
    return merged, proposals


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
            edited = base.model_copy(update={"hard_rules": HardRules.model_validate(data)})
            return _owned(edited, [name])

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
            return _owned(base.model_copy(update={"hard_rules": rules}), ["banned_phrases"])

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
            owned = "banned_phrases" if name.startswith("banned:") else name
            return _owned(base.model_copy(update={"hard_rules": hard}), [owned])

        created = await self._edit(account_id, user_id, change)
        assert created is not None
        return created

    async def rebuild(self, account_id: UUID, *, user_id: UUID | None = None) -> PassportVersion:
        """Пересобрать паспорт моделью по собственным постам → новая версия-черновик."""
        return (await self.rebuild_merged(account_id, user_id=user_id)).version

    async def rebuild_merged(self, account_id: UUID, *, user_id: UUID | None = None) -> Rebuilt:
        """Пересборка, которая не затирает ручное владельца (см. `merge_rebuild`)."""
        if self._extractor is None:
            raise StyleError("извлечение паспорта не подключено")
        fresh = await self._extractor.extract(account_id)
        current = await self._current(account_id)
        base = current.passport if current else None
        merged, proposals = merge_rebuild(base, fresh)
        created = await self.create_draft_version(
            account_id, merged, created_by="llm", user_id=user_id
        )
        return Rebuilt(created, diff_lines(base, merged), proposals)

    # --- паспорт файлом -------------------------------------------------------------------

    async def _current(self, account_id: UUID) -> PassportVersion | None:
        """Активная версия, иначе последняя."""
        active = await self.get_active(account_id)
        if active is not None:
            return active
        versions = await self.list_versions(account_id)
        return versions[-1] if versions else None

    async def preview_file(self, account_id: UUID, text: str) -> FilePreview:
        """Разобрать файл владельца и сравнить с текущим; PassportParseError — не применять."""
        current = await self._current(account_id)
        base = current.passport if current else None
        result = parse_md(text, base=base)
        return FilePreview(
            passport=result.passport,
            base_version=current.version if current else None,
            diff=diff_lines(base, result.passport),
            warnings=result.warnings,
        )

    async def apply_file(self, account_id: UUID, text: str, *, user_id: UUID) -> PassportVersion:
        """Файл владельца → новая активная версия `created_by=user` (доверенные правила).

        Жёсткие правила, отличные от прежней версии, помечаются как ручные (owner_set).
        """
        preview = await self.preview_file(account_id, text)
        current = await self._current(account_id)
        base = current.passport if current else StylePassport()
        passport = _owned(preview.passport, _changed_rules(base, preview.passport))
        passport = passport.model_copy(update={"origin": "file", "label": ""})
        async with self._uow() as uow:
            row = await self._create(
                uow.session, account_id, passport, created_by="user", user_id=user_id
            )
            return _view(await self._activate(uow.session, account_id, row, user_id))

    async def rename(
        self, account_id: UUID, version: int, label: str, *, user_id: UUID
    ) -> PassportVersion:
        """Название версии («для ответов»); меняет только служебное поле, содержимое — нет."""
        clean = " ".join(label.split())
        if not clean or len(clean) > LABEL_MAX:
            raise ValueError(f"название — от 1 до {LABEL_MAX} знаков")
        async with self._uow() as uow:
            repo = PassportsRepo(uow.session, account_id)
            row = await repo.get_by_version(version)
            if row is None:
                raise NotFound(f"паспорт v{version} не найден")
            body = {**row.body, "label": clean}
            return _view(await repo.update(row.id, body=body))

    async def is_owner(self, account_id: UUID, user_id: UUID) -> bool:
        async with self._uow() as uow:
            return await OwnerRepo(uow.session, account_id).role(user_id) == "owner"


async def _no_uow() -> Any:  # pragma: no cover - транзакцию ведёт вызывающий
    raise RuntimeError("StyleService: транзакция нужна только для методов с собственным UoW")


async def add_banned_phrases_in_session(
    session: DbSession, account_id: UUID, phrases: list[str], *, user_id: UUID
) -> PassportVersion | None:
    """Этап 13: запреты фраз из подтверждённого знания → новая активная версия паспорта.

    Работает в транзакции вызывающего. Фразы, уже запрещённые (без учёта регистра), пропускаются;
    нечего добавлять → None и версия не создаётся.
    """
    service = StyleService(_no_uow)  # type: ignore[arg-type]
    repo = PassportsRepo(session, account_id)
    base_row = await service._working_row(repo)
    base = StylePassport.model_validate(base_row.body) if base_row else StylePassport()
    current = base.hard_rules.banned_phrases
    known = {p.casefold() for p in current}
    fresh = [p.strip() for p in dict.fromkeys(phrases) if p.strip() and p.casefold() not in known]
    if not fresh:
        return None
    rules = base.hard_rules.model_copy(update={"banned_phrases": [*current, *fresh]})
    edited = _owned(base.model_copy(update={"hard_rules": rules}), ["banned_phrases"])
    row = await service._create(session, account_id, edited, created_by="user", user_id=user_id)
    row = await service._activate(session, account_id, row, user_id)
    return _view(row)
