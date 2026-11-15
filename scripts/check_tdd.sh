#!/usr/bin/env bash
# Страж TDD (раздел 1.0 ТЗ): тесты из тегов stage-NN-red не удалены и не пропущены.
# Запуск из корня репозитория: scripts/check_tdd.sh [stage-NN-red ...]
set -euo pipefail
# Тесты проекта на Python 3.13 (PEP 695 и т.п.): системный python3 может быть старше.
PY="python3"
if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then PY="uv run --no-sync python"; fi
exec $PY "$(dirname "${BASH_SOURCE[0]}")/check_tdd.py" "$@"
