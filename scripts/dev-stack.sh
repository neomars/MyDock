#!/usr/bin/env bash
# Lance la pile de démonstration avec le Python du venv de développement.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || { echo "Crée d'abord l'environnement : make venv" >&2; exit 1; }
exec "$PY" scripts/dev-stack.py "$@"
