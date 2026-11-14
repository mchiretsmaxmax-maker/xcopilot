"""Онбординг: ниша и язык → примеры стиля → конкуренты → подтверждение."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.bot.conftest import OWNER, Client, count
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork

SessionFactory = async_sessionmaker[AsyncSession]

STUDYING = "Изучаю примеры. Первые темы и черновики пришлю через пару минут."


async def _to_style(c: Client, niche: str = "Крипта", lang: str = "Русский") -> None:
    await c.say("/start")
    await c.press(niche)
    await c.press(lang)


async def _to_competitors(c: Client, samples: list[str]) -> None:
    await _to_style(c)
    for s in samples:
        await c.say(s)
    await c.press("Дальше")


async def test_niche_preset_sets_niche(owner: Client) -> None:
    await owner.say("/start")
    sent = await owner.press("Крипта")
    assert (await owner.data())["niche"] == "Крипта"
    assert sent[-1].text == "На каком языке посты?"
    assert sent[-1].buttons == ["English", "Русский"]
    assert await owner.state() == "Onboarding:lang"


async def test_niche_custom_text(owner: Client) -> None:
    await owner.say("/start")
    await owner.press("Своё")
    assert await owner.state() == "Onboarding:niche"
    sent = await owner.say("DeFi для новичков")
    assert (await owner.data())["niche"] == "DeFi для новичков"
    assert sent[-1].text == "На каком языке посты?"


async def test_language_choice_saved_to_account(
    owner: Client, session_factory: SessionFactory
) -> None:
    await _to_style(owner, lang="English")
    assert (await owner.data())["lang"] == "en"
    assert owner.last.text.startswith("Пришли 5–10 постов, которые звучат как ты")
    await owner.say("@alice")
    await owner.press("Дальше")  # мало примеров
    await owner.press("Да")
    await owner.press("Пропустить")
    await owner.press("Всё верно")
    async with UnitOfWork(session_factory) as uow:
        acc = await AccountsRepo(uow.session).get_by_handle("alice")
    assert acc is not None
    assert acc.lang == "en"
    assert acc.niche == "Крипта"


async def test_style_step_accepts_text_links_and_handle(owner: Client) -> None:
    await _to_style(owner)
    await owner.say("Мой пост про ончейн-аналитику")
    await owner.say("https://x.com/someone/status/12345")
    await owner.say("@alice")
    samples = (await owner.data())["samples"]
    assert [s["kind"] for s in samples] == ["text", "url", "handle"]
    assert samples[2]["value"] == "alice"


async def test_style_step_counter_and_low_sample_warning(owner: Client) -> None:
    await _to_style(owner)
    for i in range(3):
        sent = await owner.say(f"Пример поста номер {i}")
    assert "Принято 3 из 5" in sent[-1].text
    assert "Дальше" in sent[-1].buttons
    sent = await owner.press("Дальше")
    assert sent[-1].text == "Мало примеров — стиль получится грубым. Продолжить?"
    sent = await owner.press("Нет")
    assert await owner.state() == "Onboarding:style"
    for i in range(3, 5):
        await owner.say(f"Пример поста номер {i}")
    sent = await owner.press("Дальше")
    assert sent[-1].text.startswith("Кого изучить?")
    assert await owner.state() == "Onboarding:competitors"


async def test_competitor_handles_normalized(owner: Client) -> None:
    await _to_competitors(owner, ["раз", "два", "три", "четыре", "пять"])
    await owner.say("@Name")
    await owner.say("x.com/other")
    await owner.say("https://twitter.com/third/")
    assert (await owner.data())["competitors"] == ["name", "other", "third"]


async def test_competitor_garbage_gets_hint(owner: Client) -> None:
    await _to_competitors(owner, ["раз", "два", "три", "четыре", "пять"])
    sent = await owner.say("!!! вообще не аккаунт")
    assert sent[-1].text == "Не похоже на аккаунт X. Пришли @имя или ссылку x.com/имя."
    assert (await owner.data()).get("competitors", []) == []


async def test_competitors_step_skippable(owner: Client) -> None:
    await _to_competitors(owner, ["раз", "два", "три", "четыре", "пять"])
    sent = await owner.press("Пропустить")
    assert await owner.state() == "Onboarding:confirm"
    assert sent[-1].buttons == ["Всё верно", "Изменить"]
    assert "Крипта" in sent[-1].text


async def test_confirm_edit_restarts_from_niche(owner: Client) -> None:
    await _to_competitors(owner, ["раз", "два", "три", "четыре", "пять"])
    await owner.press("Пропустить")
    sent = await owner.press("Изменить")
    assert sent[-1].text == "В какой теме работаем?"
    assert await owner.state() == "Onboarding:niche"


async def test_confirm_creates_account_member_sources_and_jobs(
    owner: Client, session_factory: SessionFactory
) -> None:
    await _to_competitors(owner, ["Первый пост", "Второй пост", "@alice"])
    await owner.press("Да")  # мало примеров, всё равно продолжаем
    await owner.say("@bob")
    await owner.say("x.com/carol")
    await owner.press("Дальше")
    sent = await owner.press("Всё верно")
    assert sent[-1].text == STUDYING
    assert len(sent[-1].buttons) == 7  # главное меню
    assert await owner.state() is None

    async with session_factory() as s:
        acc = (
            await s.execute(text("SELECT id, handle, niche, lang FROM accounts WHERE kind='x'"))
        ).one()
        assert (acc.handle, acc.niche, acc.lang) == ("alice", "Крипта", "ru")
        member = (
            await s.execute(
                text(
                    "SELECT m.role, u.tg_user_id FROM account_members m "
                    "JOIN users u ON u.id = m.user_id WHERE m.account_id = :a"
                ),
                {"a": acc.id},
            )
        ).one()
        assert (member.role, member.tg_user_id) == ("owner", OWNER)
        sources = (
            await s.execute(
                text(
                    "SELECT kind, stance, status, author_handle FROM sources "
                    "WHERE account_id = :a ORDER BY stance, author_handle"
                ),
                {"a": acc.id},
            )
        ).all()
        # ТЗ v3.1: own_post/competitor_post → kind = post + stance own/competitor
        assert {r.kind for r in sources} == {"post"}
        assert sorted(r.stance for r in sources) == ["competitor"] * 2 + ["own"] * 3
        assert {r.status for r in sources} == {"new"}
        assert {r.author_handle for r in sources if r.stance == "competitor"} == {
            "bob",
            "carol",
        }
        jobs = (
            await s.execute(
                text("SELECT kind, payload FROM jobs WHERE account_id = :a"), {"a": acc.id}
            )
        ).all()
        assert {j.kind for j in jobs} == {"ingest.process"}
        assert len(jobs) == 5
        assert {j.payload["source_id"] for j in jobs} == {
            str(sid)
            for sid in (
                await s.execute(text("SELECT id FROM sources WHERE account_id = :a"), {"a": acc.id})
            ).scalars()
        }


async def test_confirm_without_handle_uses_generated_handle(
    owner: Client, session_factory: SessionFactory
) -> None:
    await _to_competitors(owner, ["Один", "Два"])
    await owner.press("Да")
    await owner.press("Пропустить")
    await owner.press("Всё верно")
    assert await count(session_factory, "accounts", "kind = 'x'") == 1
    assert await count(session_factory, "accounts", f"handle = 'user{OWNER}'") == 1


async def test_cancel_resets_state(owner: Client) -> None:
    await _to_style(owner)
    await owner.say("Пример")
    sent = await owner.say("/cancel")
    assert sent[-1].text == "Отменено. Чтобы начать заново, нажми /start."
    assert await owner.state() is None
    assert (await owner.data()) == {}
    sent = await owner.say("/start")
    assert sent[-1].text == "В какой теме работаем?"


async def test_cancel_without_state(owner: Client) -> None:
    sent = await owner.say("/cancel")
    assert sent[-1].text == "Нечего отменять."
