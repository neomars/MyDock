#!/bin/sh
# MyDock - lance l'assistant de lecture du Divertissement (Freetv, Kiosque) dans la session de l'écran du dock.
# Lancé par labwc (autostart), sous Wayland, à côté du navigateur : VLC doit tourner dans cette session pour
# s'afficher sur l'écran du Pi. L'assistant se connecte au dock en local (127.0.0.1, jamais le réseau) ; il ne
# lit aucun secret : l'adresse d'une chaîne ou d'un fichier lui arrive du dock au moment de lire.
# Réglage optionnel :
#   /etc/mydock-kiosk/vlc.flags   options supplémentaires de VLC, une par ligne (# = commentaire)
# Sans VLC, l'écran du dock le dit (« VLC est absent du Pi ») : sudo apt install vlc-bin vlc-plugin-base
PORT="${MYDOCK_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/"
DOCK="${MYDOCK_DOCK_BIN:-/usr/bin/mydock-dock}"

if [ ! -x "$DOCK" ] && ! command -v mydock-dock >/dev/null 2>&1; then
  echo "MyDock : mydock-dock est introuvable, pas d'assistant de lecture" >&2
  exit 1
fi
[ -x "$DOCK" ] || DOCK=$(command -v mydock-dock)

# Attendre le serveur du dock (au plus 2 minutes), comme le navigateur.
i=0
until curl -fsS --noproxy '*' "${URL}api/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 120 ] && break
  sleep 1
done

# L'assistant se reconnecte tout seul quand le dock redémarre ; cette boucle ne sert qu'à le relancer s'il s'arrête.
while true; do
  "$DOCK" player-session --url "ws://127.0.0.1:${PORT}/ws/player"
  sleep 3
done
