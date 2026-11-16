#!/usr/bin/env bash
# Дымовая проверка прод-развёртывания (этап 15, docs/runbook.md): запускать на сервере из
# корня репозитория после `up` и после каждого обновления. Код 0 — всё в порядке.
# Проверяет: права .env 600; конфигурация compose без опубликованных портов, с healthcheck,
# лимитами и внутренней сетью БД; оба сервиса healthy; приложение не под root; БД отвечает.
set -euo pipefail

export COMPOSE_FILE="${COMPOSE_FILE:-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-.env}"
fail=0
ok() { echo "✓ $*"; }
bad() { echo "✗ $*"; fail=1; }

# 1. .env: только владелец
if [[ ! -f "$ENV_FILE" ]]; then
  bad "$ENV_FILE не найден"
else
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
  [[ "$mode" == "600" ]] && ok ".env: права 600" || bad ".env: права $mode, нужно chmod 600 $ENV_FILE"
fi

# 2. статическая проверка конфигурации (та же, что в тестах: xcopilot.ops.security)
if docker compose config --format json \
  | docker compose run --rm -T --no-deps app python -m xcopilot.ops.security; then
  ok "конфигурация compose"
else
  bad "конфигурация compose (см. выше)"
fi
published="$(docker compose ps --format '{{.Name}} {{.Publishers}}' | grep -E '[0-9]+->' || true)"
[[ -z "$published" ]] && ok "наружу не опубликован ни один порт" || bad "опубликованы порты: $published"

# 3. сервисы живы
for svc in db app; do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null || echo none)"
  [[ "$status" == "healthy" ]] && ok "$svc: healthy" || bad "$svc: $status"
done

# 4. приложение под непривилегированным пользователем
uid="$(docker compose exec -T app id -u 2>/dev/null || echo '?')"
[[ "$uid" != "0" && "$uid" != "?" ]] && ok "app: uid $uid (не root)" || bad "app: uid $uid"

# 5. БД отвечает изнутри приложения
docker compose exec -T app python -m xcopilot.healthcheck && ok "БД отвечает" || bad "БД не отвечает"

exit "$fail"
