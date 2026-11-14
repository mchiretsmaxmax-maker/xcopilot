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
BUDGET_WAIT = "Хорошо, ждём до завтра."

_LANG_NAMES = {"en": "English", "ru": "Русский"}
_XMODE_NAMES = {"off": "выключен", "owned": "только мой аккаунт", "paid": "платное чтение"}


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


# --- Доступ к X (этап 6) ---

X_ASK_CLIENT_ID = (
    "Пришли Client ID приложения X: developer.x.com → твоё приложение → "
    "Keys and tokens → OAuth 2.0 Client ID and Client Secret."
)
X_BAD_CLIENT_ID = "Не похоже на Client ID: одна строка без пробелов. Пришли ещё раз."
X_ASK_CLIENT_SECRET = (
    "Теперь Client Secret. Сообщение с ним я сразу удалю, а сам секрет сохраню зашифрованным."
)
X_BAD_CLIENT_SECRET = "Не похоже на Client Secret: одна строка без пробелов. Пришли ещё раз."
X_NO_SECRETS_KEY = "Подключение X недоступно: на сервере не задан SECRETS_KEY. Сообщи владельцу."
X_BAD_CALLBACK = "Это не похоже на адрес страницы. Скопируй адрес целиком и пришли сюда."
X_UNAVAILABLE = "X сейчас не отвечает. Пришли адрес ещё раз чуть позже."
X_CANCELLED = "Хорошо, режим доступа к X не меняю."


def x_access_summary(mode: str, connected: bool) -> str:
    state = "подключён" if connected else "не подключён"
    return f"Доступ к X: {_XMODE_NAMES.get(mode, mode)}. Аккаунт X {state}.\nКак читать X?"


def x_auth_link(url: str, redirect_uri: str) -> str:
    return (
        f"Открой ссылку и разреши доступ (только чтение):\n{url}\n\n"
        f"В настройках приложения X адрес возврата должен быть {redirect_uri}.\n"
        "После входа скопируй адрес страницы, на которую тебя перебросит, и пришли сюда."
    )


def x_connected(mode: str, handle: str | None) -> str:
    who = f" как @{handle}" if handle else ""
    return f"X подключён{who}. Режим: {_XMODE_NAMES.get(mode, mode)}."


def x_connected_without_id(mode: str) -> str:
    return (
        f"X подключён, режим: {_XMODE_NAMES.get(mode, mode)}. Не удалось узнать id аккаунта — "
        "свои посты подтянутся после повторного подключения."
    )


def x_auth_failed(reason: str) -> str:
    return f"Подключить X не вышло: {reason} Начни заново: /settings → «Доступ к X»."


def usd_exact(micro: int) -> str:
    """Микродоллары → «$0.005», «$1», «$2.5» (без округления до цента)."""
    value = (Decimal(micro) / 1_000_000).normalize()
    return f"${value:f}"


def x_paid_estimate(post_micro: int, profile_micro: int, limit_micro: int) -> str:
    posts = limit_micro // post_micro if post_micro else 0
    return (
        f"Чужой пост — {usd_exact(post_micro)}, профиль — {usd_exact(profile_micro)}. "
        f"При лимите {usd_exact(limit_micro)} в день это до {posts} постов.\n"
        "Включить платное чтение?"
    )


# --- ингест (этап 7) ------------------------------------------------------------------------


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 пост, 2 поста, 5 постов."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


INGEST_EMPTY = "Пришли текст поста, ссылку на пост в X, @аккаунт или файл."
INGEST_FILE_TOO_BIG = "Файл больше 20 МБ — не возьму. Пришли файл поменьше или текст."
INGEST_TEXT_TOO_LONG = "Текст длиннее 50 000 знаков. Разбей его на части и пришли по очереди."
INGEST_UNSUPPORTED = "Такой формат не беру: пришли txt, md, pdf, docx или result.json из Telegram."
INGEST_ALL_DUPLICATES = "Это уже есть в базе, повторно не беру."
INGEST_FILE_DUPLICATE = "Этот файл уже был, повторно не беру."
INGEST_OWN_HANDLE_DUPLICATE = "Твои посты из X уже в работе."
INGEST_SEND_TEXT = "Пришли посты текстом: можно несколько сразу, разделяя их строкой ---."
INGEST_NO_ACCOUNT = "Сначала пройди настройку: /start."
INGEST_FILE_LOST = "Файл потерялся по дороге. Пришли его ещё раз."
INGEST_EMPTY_TEXT = "В присланном нет текста."
INGEST_KIND_QUESTION = "Это что? Если я ошибся — выбери."
KIND_LABELS = {
    "own_post": "Мой пост",
    "liked_sample": "Нравится стиль",
    "competitor_post": "Конкурент",
    "note": "Материал и факты",
}


def ingest_accepted_posts(n: int, dups: int) -> str:
    text = f"Принял {n} {plural(n, 'пост', 'поста', 'постов')}. Изучаю…"
    return text + (f" Дублей: {dups}." if dups else "")


def ingest_accepted_links(n: int, dups: int) -> str:
    text = f"Принял {n} {plural(n, 'ссылку', 'ссылки', 'ссылок')}. Изучаю…"
    return text + (f" Дублей: {dups}." if dups else "")


def ingest_accepted_file(name: str) -> str:
    return f"Принял файл «{name}». Изучаю…"


def ingest_accepted_handle(handle: str) -> str:
    return f"Читаю посты @{handle}. Изучаю…"


def ingest_foreign_handle(handle: str) -> str:
    return (
        f"Посты @{handle} я могу прочитать только в режиме платного чтения X. "
        "Пришли их текстом — или включи платное чтение."
    )


def ingest_post_not_found(url: str) -> str:
    return f"Не нашёл пост по ссылке {url}. Проверь ссылку или пришли текст."


def ingest_parse_failed(name: str, reason: str) -> str:
    return f"Не смог разобрать «{name}»: {reason}"


def ingest_x_off() -> str:
    return "Чтение X выключено. Пришли посты текстом или включи доступ к X в настройках."


def ingest_kind_set(kind: str) -> str:
    return f"Запомнил: {KIND_LABELS.get(kind, kind)}."


_KIND_PHRASES = {
    "own_post": ("пример", "примера", "примеров", " твоего стиля"),
    "liked_sample": ("образец", "образца", "образцов", " чужого стиля"),
    "competitor_post": ("пост", "поста", "постов", " конкурентов"),
    "note": ("материал", "материала", "материалов", ""),
}


def ingest_summary(
    by_kind: dict[str, int], total_style: int, topics: list[str], dups: int, ask_kind: bool
) -> str:
    """«Готово: +3 примера твоего стиля (всего 14). Темы: …. Дублей: 1.» (+ вопрос «Это что?»)."""
    parts: list[str] = []
    for kind, n in by_kind.items():
        if not n or kind not in _KIND_PHRASES:
            continue
        one, few, many, tail = _KIND_PHRASES[kind]
        part = f"+{n} {plural(n, one, few, many)}{tail}"
        if kind == "own_post":
            part += f" (всего {total_style})"
        parts.append(part)
    text = "Готово: " + (", ".join(parts) if parts else "нового нет") + "."
    if topics:
        text += f" Темы: {', '.join(topics)}."
    text += f" Дублей: {dups}."
    if ask_kind and parts:
        text += f"\n\n{INGEST_KIND_QUESTION}"
    return text


def ingest_file_summary(pages: int | None, chunks: int, topics: list[str], dups: int) -> str:
    """«Готово. В файле 42 страницы, взял 118 фрагментов. Темы: …»."""
    frags = f"{chunks} {plural(chunks, 'фрагмент', 'фрагмента', 'фрагментов')}"
    if pages:
        word = plural(pages, "страница", "страницы", "страниц")
        text = f"Готово. В файле {pages} {word}, взял {frags}."
    else:
        text = f"Готово. Из файла взял {frags}."
    if topics:
        text += f" Темы: {', '.join(topics)}."
    if dups:
        text += f" Повторов пропустил: {dups}."
    return text


LEARN_HINT = (
    "Пришли, на чём учиться: текст поста (несколько — через строку ---), ссылки на посты в X, "
    "свой @аккаунт или файл (txt, md, pdf, docx, экспорт Telegram) до 20 МБ."
)


WHY_EMPTY = "Пока нет ни одного запуска генерации — смотреть нечего."
WHY_ITEM_LIMIT = 80
WHY_TEXT_LIMIT = 4000


def _clip(value: str, limit: int = WHY_ITEM_LIMIT) -> str:
    """Одна строка не длиннее limit знаков: переводы строк → пробелы, хвост → «…»."""
    value = value.replace("\n", " ").strip()
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def why_report(
    *,
    passport_version: int | None,
    prompt_label: str | None,
    tokens: int,
    budget_tokens: int,
    examples: list[str],
    knowledge: list[str],
    facts: list[str],
    anti_examples: list[str],
    warnings: list[str],
) -> str:
    """Отчёт /why: что писатель видел в последнем запуске."""
    lines: list[str] = []
    lines.append(
        f"Паспорт: v{passport_version}" if passport_version is not None else "Паспорт: нет"
    )
    lines.append(f"Промпт: {prompt_label}" if prompt_label is not None else "Промпт: —")
    lines.append(f"Пакет: {tokens} из {budget_tokens} токенов")

    blocks: list[tuple[str, list[str], bool]] = [
        ("Примеры голоса", examples, True),
        ("Знания", knowledge, False),
        ("Факты", facts, False),
        ("Антипримеры", anti_examples, True),
    ]

    for title, items, numbered in blocks:
        if not items:
            continue
        lines.append("")
        lines.append(f"{title} ({len(items)}):")
        for i, item in enumerate(items, start=1):
            clipped = _clip(item)
            lines.append(f"{i}. {clipped}" if numbered else f"• {clipped}")

    if warnings:
        lines.append("")
        lines.append("Предупреждения: " + ", ".join(warnings))

    return "\n".join(lines)[:WHY_TEXT_LIMIT]
