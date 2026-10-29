#!/usr/bin/env bash
# MyDock - désinstalle l'agent du PC.
# Usage : ./deploy/pc/uninstall-agent.sh [--purge] [--dry-run]
#   --purge  supprime aussi l'environnement Python ET la configuration (donc le jeton).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib.sh
. "$HERE/../lib.sh"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Option inconnue : $arg" ;;
  esac
done

MYDOCK_HOME=${MYDOCK_HOME:-$HOME/.local/share/mydock}
CONFIG_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/mydock
UNIT=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/mydock-agent.service

step "Service"
if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
  run systemctl --user disable --now mydock-agent.service 2>/dev/null || true
fi
run rm -f "$UNIT"
if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
  run systemctl --user daemon-reload
fi
ok "Service retiré"

if [ "$PURGE" = 1 ]; then
  step "Suppression des données"
  run rm -rf "$MYDOCK_HOME" "$CONFIG_DIR"
  ok "Environnement Python, configuration et jeton supprimés"
else
  say "Environnement ($MYDOCK_HOME) et configuration ($CONFIG_DIR) conservés ; ajoute --purge pour les supprimer."
fi
say "Si tu avais une règle ufw : sudo ufw status numbered, puis sudo ufw delete <numéro> (commentaire « MyDock agent »)."
