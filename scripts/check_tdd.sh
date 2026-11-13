#!/usr/bin/env bash
# Страж TDD (раздел 1.0 ТЗ): тесты из тегов stage-NN-red не удалены и не пропущены.
# Запуск из корня репозитория: scripts/check_tdd.sh [stage-NN-red ...]
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/check_tdd.py" "$@"
