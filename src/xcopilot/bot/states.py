"""Состояния диалогов (FSM)."""

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    niche = State()
    lang = State()
    style = State()
    competitors = State()
    confirm = State()


class SettingsFlow(StatesGroup):
    tz = State()
    limit_llm = State()
    limit_x = State()


class XConnect(StatesGroup):
    client_id = State()
    client_secret = State()
    callback = State()
