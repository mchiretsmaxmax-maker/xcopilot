"""Состояния диалогов (FSM)."""

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    niche = State()
    lang = State()
    style = State()
    competitors = State()
    confirm = State()
    rules = State()  # 9C: «что у тебя точно работает»


class SettingsFlow(StatesGroup):
    tz = State()
    limit_llm = State()
    limit_x = State()
    handle = State()


class XConnect(StatesGroup):
    client_id = State()
    client_secret = State()
    callback = State()


class WriteFlow(StatesGroup):
    """«✍️ Написать»: формат → (целевой пост) → тема."""

    topic = State()
    target = State()


class TrendFlow(StatesGroup):
    post = State()


class MaterialFlow(StatesGroup):
    """Этап 7A: ждём цифры текстом для материала (кнопка «🔢 Цифры»)."""

    numbers = State()


class BrainFlow(StatesGroup):
    """Этап 9C: ждём новый текст знания (кнопка «✏️ Изменить»)."""

    edit = State()
    add = State()  # «➕ Добавить правило» из раздела формата


class CardFlow(StatesGroup):
    """Этап 12: правка текста, своя подсказка «Ещё вариант», ссылка на опубликованный пост."""

    edit = State()
    regen_hint = State()
    link = State()


class ReplyFlow(StatesGroup):
    """«💬 Ответы»: ждём ссылку на пост (link) или его текст, если чтение не удалось (manual)."""

    link = State()
    manual = State()


class StyleFlow(StatesGroup):
    """«🎨 Стиль»: ждём файл паспорта (upload), подтверждение (confirm), название версии (name)."""

    upload = State()
    confirm = State()
    name = State()
    format_rules = State()  # ждём новый текст правил формата (в data — "format")
