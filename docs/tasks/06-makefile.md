## Файлы
- (нет)

## Новые
- Makefile

## Контекст
- compose.yaml
- pyproject.toml

## Задача
Создай `Makefile` (отступы рецептов — табы). Первая строка: `.PHONY: up down test lint fmt migrate db-reset db-docs`. `.DEFAULT_GOAL := help`. Цели:
- `help`: напечатать список целей с однострочным описанием (`@grep`/`awk` по комментариям `## ` после имени цели).
- `up`: `docker compose up -d --build` (описание: «поднять app и db»).
- `down`: `docker compose down`.
- `test`: `uv run pytest` (покрытие уже в addopts pyproject).
- `lint`: три команды подряд: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- `fmt`: `uv run ruff check --fix .` и `uv run ruff format .`.
- `migrate`: заглушка: `@echo "migrate: миграции появятся на этапе 2"`.
- `db-reset`: `docker compose down -v` (удалить данные БД), затем `docker compose up -d db`.
- `db-docs`: заглушка: `@echo "db-docs: генерация docs/db появится на этапе 2"`.
Никаких других целей и переменных.

## Коммит
feat: Makefile
