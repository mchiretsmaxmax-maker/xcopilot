.PHONY: help up down test lint fmt migrate db-reset db-docs backup restore setup start stop status logs export import
.DEFAULT_GOAL := help

help: ## показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

up: ## поднять db, накатить миграции, поднять app
	docker compose up -d --build --wait db
	docker compose run --rm --no-deps app python -m xcopilot.db.migrate
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

migrate: ## применить миграции и создать месячные партиции
	docker compose run --rm --no-deps app python -m xcopilot.db.migrate

db-reset: ## пересоздать БД с нуля (данные удаляются!) и накатить миграции
	docker compose down -v --remove-orphans
	docker compose up -d --wait db
	docker compose run --rm --no-deps app python -m xcopilot.db.migrate

db-docs: ## сгенерировать docs/db/schema.md, er.md и metrics.md
	uv run python -m xcopilot.db.docs
	uv run python -m xcopilot.metrics.docs

backup: ## бэкап БД в backups/ (pg_dump -Fc)
	set -a && . ./.env && set +a && scripts/backup.sh

restore: ## восстановить БД из дампа: make restore FILE=backups/….dump
	set -a && . ./.env && set +a && scripts/restore.sh "$(FILE)"

setup: ## подготовить устройство: зависимости, БД, миграции, проверка claude, служба
	scripts/setup.sh

start: ## запустить бота (служба автозапуска)
	scripts/service.sh start

stop: ## остановить бота
	scripts/service.sh stop

status: ## состояние службы
	scripts/service.sh status

logs: ## хвост logs/bot.log
	scripts/service.sh logs

export: ## пакет переноса на другое устройство (бот здесь остановится)
	scripts/transfer-export.sh

import: ## развернуть пакет на этом устройстве: make import FILE=xcopilot-….xcbundle
	scripts/transfer-import.sh "$(FILE)"
