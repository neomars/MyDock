#!/usr/bin/env bash
# Vérifie que dock/web/dist (commité, pour que le Pi n'ait pas besoin de Node) correspond
# bien aux sources. À lancer avant de committer un changement dans dock/web/src.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../dock/web"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
npx vite build --outDir "$tmp/dist" --emptyOutDir >/dev/null
if diff -r "$tmp/dist" dist >/dev/null; then
  echo "✔ dock/web/dist correspond aux sources."
else
  echo "✘ dock/web/dist ne correspond plus aux sources : lance « make web » puis commite le dossier." >&2
  exit 1
fi
