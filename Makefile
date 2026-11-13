.PHONY: up down test lint fmt migrate db-reset db-docs
.DEFAULT_GOAL := help

help: ## показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

up: ## поднять app и db
	docker compose up -d --build

down: ## остановить сервисы
	docker compose down

test: ## запустить тесты
	uv run pytest

lint: ## проверить код линтерами и типами
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt: ## отформатировать код
	uv run ruff check --fix .
	uv run ruff format .

migrate: ## применить миграции
	@echo "migrate: миграции появятся на этапе 2"

db-reset: ## сбросить данные БД
	docker compose down -v
	docker compose up -d db

db-docs: ## сгенерировать документацию БД
	@echo "db-docs: генерация docs/db появится на этапе 2"
