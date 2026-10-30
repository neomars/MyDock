#!/bin/sh
# MyDock - éteint l'écran du dock après l'inactivité choisie dans les Réglages, le rallume au toucher.
# Lancé par labwc (autostart), sous Wayland, à côté du navigateur :
#   - swayidle surveille les touchers ; il rappelle ce même script avec --off (extinction) puis --on ;
#   - la durée vient du dock (GET /api/screen) ; si elle change dans les Réglages, swayidle est relancé ;
#   - l'écran s'éteint avec wlopm (gestion d'énergie Wayland) ou, à défaut, avec wlr-randr.
# Sans swayidle l'écran ne s'éteint pas vraiment (l'interface le noircit quand même) :
#   sudo apt install swayidle
PORT="${MYDOCK_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/"
POLL="${MYDOCK_SCREEN_POLL:-15}"
SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")

# set_power on|off : allume ou éteint tous les écrans.
set_power() {
  if command -v wlopm >/dev/null 2>&1; then
    wlopm "--$1" '*'
  elif command -v wlr-randr >/dev/null 2>&1; then
    wlr-randr 2>/dev/null | awk '/^[^ ]/ {print $1}' | while read -r output; do
      wlr-randr --output "$output" "--$1"
    done
  fi
}

case "${1:-}" in
  --on | --off)
    set_power "${1#--}"
    exit 0
    ;;
esac

if ! command -v swayidle >/dev/null 2>&1; then
  echo "MyDock : swayidle est absent, l'écran du dock ne s'éteindra pas (sudo apt install swayidle)" >&2
  exit 0
fi
if ! command -v wlopm >/dev/null 2>&1 && ! command -v wlr-randr >/dev/null 2>&1; then
  echo "MyDock : ni wlopm ni wlr-randr, impossible d'éteindre l'écran du dock (sudo apt install wlr-randr)" >&2
  exit 0
fi

# La durée choisie, en secondes (rien si le dock ne répond pas : on garde alors ce qui est en place).
wanted() {
  curl -fsS --noproxy '*' -m 3 "${URL}api/screen" 2>/dev/null |
    sed -n 's/.*"sleep_after": *\([0-9][0-9]*\).*/\1/p'
}

applied=""
idle_pid=""
sleeper=""

# Arrête swayidle et rallume l'écran : on ne laisse jamais l'écran éteint sans personne pour le rallumer.
stop_idle() {
  if [ -n "$idle_pid" ]; then
    kill "$idle_pid" 2>/dev/null
    wait "$idle_pid" 2>/dev/null
    idle_pid=""
    set_power on
  fi
}
trap 'stop_idle; [ -n "$sleeper" ] && kill "$sleeper" 2>/dev/null; exit 0' INT TERM HUP

while true; do
  # swayidle a pu s'arrêter tout seul (changement d'écran, plantage) : on le relancera.
  if [ -n "$idle_pid" ] && ! kill -0 "$idle_pid" 2>/dev/null; then
    idle_pid=""
    applied=""
  fi
  seconds=$(wanted)
  if [ -n "$seconds" ] && [ "$seconds" != "$applied" ]; then
    stop_idle
    if [ "$seconds" -gt 0 ]; then
      swayidle -w timeout "$seconds" "'$SELF' --off" resume "'$SELF' --on" &
      idle_pid=$!
    fi
    applied=$seconds
  fi
  sleep "$POLL" &
  sleeper=$!
  wait "$sleeper" # attendre ainsi laisse le trap répondre tout de suite à un arrêt
done
