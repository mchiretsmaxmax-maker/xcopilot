"""Все строки интерфейса бота."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DENIED = "Доступ закрыт. Попроси владельца добавить тебя."
NO_ACCOUNT = "Сначала настроим аккаунт: нажми /start."
FLOOD = "Слишком часто. Подожди немного."
CANCELLED = "Отменено. Чтобы начать заново, нажми /start."
NOTHING_TO_CANCEL = "Нечего отменять."
MENU = "Главное меню. Что делаем?"
COMING_SOON = "Этот раздел появится на следующих этапах."

ASK_NICHE = "В какой теме работаем?"
ASK_NICHE_CUSTOM = "Опиши тему своими словами, например: «DeFi для новичков»."
ASK_LANG = "На каком языке посты?"
ASK_STYLE = "Пришли 5–10 постов, которые звучат как ты: текстом, ссылками или ссылкой на аккаунт."
STYLE_LOW = "Мало примеров — стиль получится грубым. Продолжить?"
STYLE_MORE = "Хорошо, пришли ещё примеры."
STYLE_NEED_ONE = "Нужен хотя бы один пример: текст, ссылка на пост или ссылка на аккаунт."
STYLE_FULL = "Это максимум — 10 примеров. Нажми «Дальше»."
ASK_COMPETITORS = "Кого изучить? Пришли @аккаунты или нажми «Пропустить»."
COMPETITOR_HINT = "Не похоже на аккаунт X. Пришли @имя или ссылку x.com/имя."
STUDYING = "Изучаю примеры. Первые темы и черновики пришлю через пару минут."

HELP = (
    "Я помогаю вести аккаунт в X: изучаю твой стиль, предлагаю темы и готовлю черновики. "
    "Публикуешь всегда ты.\n\n"
    "/start — начать или открыть меню\n"
    "/settings — часовой пояс, язык, лимиты, режим X\n"
    "/cost — расход за сегодня и месяц\n"
    "/cancel — отменить текущий шаг\n"
    "/help — эта справка"
)

ASK_TZ = "Пришли название часового пояса, например Europe/Minsk."
BAD_TZ = "Не знаю такой пояс. Пример: Europe/Berlin."
ASK_LIMIT = "Пришли дневной лимит в долларах, например 5 или 7.5."
BAD_LIMIT = "Нужно число в долларах от 0.000001 до 1000, например 5 или 7.5."
CHOOSE_LANG = "На каком языке писать посты?"
CHOOSE_LIMIT = "Какой лимит меняем?"
CHOOSE_XMODE = "Как читать X?"
BUDGET_WAIT = "Хорошо, ждём до завтра."

_LANG_NAMES = {"en": "English", "ru": "Русский"}
_XMODE_NAMES = {"off": "выключен", "owned": "только свои посты", "paid": "платный"}


def lang_name(code: str) -> str:
    return _LANG_NAMES.get(code, code)


def fmt_usd(micro: int) -> str:
    """Микродоллары → «$1.50» (округление до цента)."""
    value = (Decimal(micro) / 1_000_000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${value}"


def error(code: str) -> str:
    return f"Что-то пошло не так. Код ошибки: {code}. Попробуй ещё раз позже."


def style_counter(n: int) -> str:
    return f"Принято {n} из 5" if n < 5 else f"Принято {n} из 10"


def competitors_added(handles: list[str], garbage: list[str]) -> str:
    text = "Добавил: " + ", ".join(f"@{h}" for h in handles) + "."
    if garbage:
        text += " Не распознано: " + ", ".join(garbage) + "."
    return text + " Пришли ещё или нажми «Дальше»."


def confirm_summary(niche: str, lang: str, samples: int, competitors: list[str]) -> str:
    comps = ", ".join(f"@{h}" for h in competitors) if competitors else "—"
    return (
        "Проверь:\n"
        f"Тема: {niche}\n"
        f"Язык постов: {lang_name(lang)}\n"
        f"Примеров стиля: {samples}\n"
        f"Конкуренты: {comps}"
    )


def settings_summary(tz: str, lang: str, x_mode: str) -> str:
    return (
        "Настройки:\n"
        f"Часовой пояс: {tz}\n"
        f"Язык постов: {lang_name(lang)}\n"
        f"Режим X: {_XMODE_NAMES.get(x_mode, x_mode)}"
    )


def tz_saved(tz: str) -> str:
    return f"Часовой пояс: {tz}."


def lang_saved(lang: str) -> str:
    return f"Язык постов: {lang_name(lang)}."


def limit_saved(what: str, micro: int) -> str:
    return f"Дневной лимит {what}: {fmt_usd(micro)}."


def xmode_saved(mode: str) -> str:
    return f"Режим X: {_XMODE_NAMES.get(mode, mode)}."


def cost_report(
    *,
    today_roles: dict[str, int],
    today_x: int,
    month_roles: dict[str, int],
    month_x: int,
    llm_remaining: int,
    llm_limit: int,
    x_remaining: int,
    x_limit: int,
) -> str:
    def models(roles: dict[str, int]) -> str:
        total = sum(roles.values())
        parts = ", ".join(
            f"{role} {fmt_usd(v)}" for role, v in sorted(roles.items(), key=lambda kv: -kv[1])
        )
        return f"Модели: {fmt_usd(total)}" + (f" ({parts})" if parts else "")

    return (
        "Сегодня\n"
        f"{models(today_roles)}\n"
        f"X: {fmt_usd(today_x)}\n"
        f"Остаток дневного лимита моделей: {fmt_usd(llm_remaining)} из {fmt_usd(llm_limit)}\n"
        f"Остаток дневного лимита X: {fmt_usd(x_remaining)} из {fmt_usd(x_limit)}\n"
        "\nМесяц\n"
        f"{models(month_roles)}\n"
        f"X: {fmt_usd(month_x)}"
    )


_SCOPE_EXCEEDED = {"llm": "на модели", "x_api": "на X", "sniper": "на хвост тренда"}
_SCOPE_WARNING = {"llm": "моделей", "x_api": "на X", "sniper": "на хвост тренда"}


def budget_exceeded(scope: str, period: str, spent: int, limit: int) -> str:
    what = _SCOPE_EXCEEDED.get(scope, scope)
    kind = "Дневной" if period == "day" else "Месячный"
    return f"{kind} лимит {what} исчерпан ({fmt_usd(spent)} из {fmt_usd(limit)})."


def budget_warning(scope: str, period: str, spent: int, limit: int) -> str:
    what = _SCOPE_WARNING.get(scope, scope)
    kind = "дневному" if period == "day" else "месячному"
    return f"Расход {what} подходит к {kind} лимиту ({fmt_usd(spent)} из {fmt_usd(limit)})."


def budget_raised(scope: str, micro: int) -> str:
    return f"Поднял лимит ({scope}) до {fmt_usd(micro)}."
