"""Общие исключения (заглушка до зелёного)."""


class AppError(Exception):
    pass


class ConfigError(AppError):
    pass
