#!/usr/bin/env bash
# Crée .venv-ubuntu : Python 3.14 et les versions EXACTES des paquets Python d'Ubuntu 26.04.
#
# Le paquet .deb de l'agent s'appuie sur les paquets python3-* de la distribution, pas sur pip.
# Un environnement de développement à jour (fastapi 0.16x, starlette 1.x…) ne prouve donc pas que
# l'agent tourne chez l'utilisateur : celui-ci reproduit ce que apt y installera.
#
# Usage : scripts/ubuntu-venv.sh [dossier]      (défaut : .venv-ubuntu)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${1:-$ROOT/.venv-ubuntu}
CONSTRAINTS=$ROOT/packaging/ubuntu-26.04-constraints.txt

if command -v uv >/dev/null 2>&1; then
  pip_install() { uv pip install --quiet --python "$VENV/bin/python" "$@"; }
  [ -x "$VENV/bin/python" ] || uv venv --quiet --python 3.14 "$VENV"
elif command -v python3.14 >/dev/null 2>&1; then
  pip_install() { "$VENV/bin/python" -m pip install --quiet "$@"; }
  [ -x "$VENV/bin/python" ] || python3.14 -m venv "$VENV"
else
  echo "Python 3.14 est introuvable (installe-le, ou installe « uv »)." >&2
  exit 1
fi

# fastapi 0.118.0 borne starlette < 0.49 alors qu'Ubuntu livre 0.50.0 avec lui : on reproduit la
# combinaison d'Ubuntu en installant fastapi après les autres, sans réévaluer ses dépendances.
pip_install -c "$CONSTRAINTS" starlette uvicorn websockets psutil pydantic tomli-w anyio h11 \
  typing-extensions httpx dbus-fast
pip_install -c "$CONSTRAINTS" --no-deps fastapi
pip_install -c "$CONSTRAINTS" --no-deps -e "$ROOT/common" -e "$ROOT/agent" -e "$ROOT/dock/server"
pip_install -c "$CONSTRAINTS" pytest pytest-asyncio ruff pillow
"$VENV/bin/python" - <<'PY'
from importlib.metadata import version

for name in ("fastapi", "starlette", "uvicorn", "websockets", "psutil", "pydantic", "tomli-w", "dbus-fast", "pillow"):
    print(f"  {name:12} {version(name)}")
PY
