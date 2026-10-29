#!/usr/bin/env bash
# MyDock - désinstalle le dock du Raspberry Pi. À lancer avec sudo.
# Usage : sudo ./deploy/pi/uninstall-dock.sh [--purge] [--dry-run]
#   --purge  supprime aussi la configuration (jeton compris), l'état et les utilisateurs système.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib.sh
. "$HERE/../lib.sh"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Option inconnue : $arg" ;;
  esac
done
[ "$(id -u)" = 0 ] || die "Lance ce script avec sudo."

step "Services"
for unit in mydock-kiosk mydock-dock; do
  run systemctl disable --now "$unit.service" 2>/dev/null || true
  run rm -f "/etc/systemd/system/$unit.service"
done
run systemctl daemon-reload
ok "Services retirés"

step "Kiosque en mode bureau"
for home in /home/*; do
  autostart=$home/.config/labwc/autostart
  if [ -f "$autostart" ] && grep -q 'MyDock' "$autostart"; then
    if [ -f "$autostart.avant-mydock" ]; then
      run mv -f "$autostart.avant-mydock" "$autostart"
      say "  Autostart d'origine restauré : $autostart"
    else
      run rm -f "$autostart"
      say "  Autostart MyDock retiré : $autostart"
    fi
  fi
done

step "Fichiers"
run rm -rf /opt/mydock /etc/mydock-kiosk
if [ "$PURGE" = 1 ]; then
  run rm -rf /etc/mydock /var/lib/mydock /var/lib/mydock-kiosk
  run userdel mydock 2>/dev/null || true
  run userdel mydock-kiosk 2>/dev/null || true
  ok "Configuration, jeton, état et utilisateurs supprimés"
else
  say "Configuration (/etc/mydock) et état (/var/lib/mydock) conservés ; ajoute --purge pour les supprimer."
fi
say "Si tu avais désactivé le gestionnaire de session : sudo systemctl enable lightdm && sudo systemctl set-default graphical.target"
