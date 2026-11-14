#!/bin/sh
# MyDock - lance Chromium en kiosque sur l'interface du dock, le relance s'il se ferme, et aussi s'il reste gris.
# Lancé par labwc (autostart), sous Wayland. Réglages optionnels :
#   /etc/mydock-kiosk/display.conf   MYDOCK_OUTPUT_MODE="1024x600@60"   (force le mode de l'écran)
#   /etc/mydock-kiosk/chromium.flags options supplémentaires de Chromium, une par ligne (# = commentaire)
#
# Au démarrage du Pi, Chromium reste parfois en vie sans rien afficher : un écran gris et le pointeur de la souris. Ce
# lanceur s'en défend à quatre endroits :
#   - un seul lanceur à la fois (verrou) : en lancer un deuxième à la main n'ouvre pas une deuxième fenêtre sur le même profil ;
#   - il attend que le compositeur ait un écran avant d'ouvrir la fenêtre ;
#   - avant chaque lancement, il arrête un Chromium resté sur ce profil et efface un verrou de profil périmé (coupure de courant) ;
#   - après chaque lancement, il vérifie que la page s'est vraiment affichée : la fenêtre existe (wlrctl) ET le dock voit le
#     navigateur connecté (/api/health, champ « browsers »). Sinon, au bout d'une minute, il arrête Chromium et le relance ;
#     après trois échecs de suite, il le relance sans accélération graphique.
PORT="${MYDOCK_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/"
CONF_DIR="${MYDOCK_CONF_DIR:-/etc/mydock-kiosk}"
PROFILE="${MYDOCK_PROFILE:-${XDG_CACHE_HOME:-$HOME/.cache}/mydock-chromium}"
LOCK="${MYDOCK_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/mydock-kiosk.lock}"
SHOW_TIMEOUT="${MYDOCK_SHOW_TIMEOUT:-60}" # secondes accordées à la page pour s'afficher
WATCH_STEP="${MYDOCK_WATCH_STEP:-2}"      # secondes entre deux vérifications (entier)
OUTPUT_WAIT="${MYDOCK_OUTPUT_WAIT:-30}"   # secondes d'attente d'un écran au démarrage du compositeur
PAUSE="${MYDOCK_RETRY_PAUSE:-2}"          # secondes entre deux lancements (plus longues après trois échecs de suite)

say() { echo "MyDock : $*" >&2; }

for candidate in chromium chromium-browser; do
  if command -v "$candidate" >/dev/null 2>&1; then CHROMIUM=$candidate; break; fi
done
[ -n "${CHROMIUM:-}" ] || { say "Chromium est introuvable (sudo apt install chromium)"; exit 1; }

# Un seul lanceur à la fois : le verrou est tenu par le lanceur qui travaille, et tombe avec lui.
if [ -z "${MYDOCK_KIOSK_LOCKED:-}" ] && command -v flock >/dev/null 2>&1; then
  MYDOCK_KIOSK_LOCKED=1 flock -n -E 75 "$LOCK" /bin/sh "$0" "$@"
  status=$?
  if [ "$status" -eq 75 ]; then
    say "le lanceur du kiosque tourne déjà (pour relancer l'écran : sudo pkill -x chromium, il repart tout seul)"
    exit 0
  fi
  exit "$status"
fi

# Mode de l'écran (le Waveshare 7" 1024x600 est normalement reconnu tout seul).
if [ -r "$CONF_DIR/display.conf" ]; then
  # shellcheck disable=SC1091
  . "$CONF_DIR/display.conf"
fi

# Attendre que le compositeur ait un écran : une fenêtre ouverte avant reste grise.
if command -v wlr-randr >/dev/null 2>&1; then
  i=0
  until wlr-randr 2>/dev/null | grep -q '^[^ ]'; do
    i=$((i + 1))
    [ "$i" -gt "$OUTPUT_WAIT" ] && break
    sleep 1
  done
fi
if [ -n "${MYDOCK_OUTPUT_MODE:-}" ] && command -v wlr-randr >/dev/null 2>&1; then
  OUTPUT=$(wlr-randr 2>/dev/null | awk '/^[^ ]/ {print $1; exit}')
  if [ -n "$OUTPUT" ]; then wlr-randr --output "$OUTPUT" --mode "$MYDOCK_OUTPUT_MODE" || true; fi
fi

# Attendre le serveur du dock (au plus 2 minutes) pour ne jamais afficher une page d'erreur.
i=0
until curl -fsS --noproxy '*' "${URL}api/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 120 ] && break
  sleep 1
done

# Un Chromium resté sur ce profil (lanceur arrêté, session relancée) empêcherait le nôtre de s'ouvrir ou serait le gris
# lui-même ; un verrou laissé par une coupure de courant peut désigner un numéro de processus réutilisé depuis.
stop_orphans() {
  if command -v pkill >/dev/null 2>&1 && pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null; then
    n=0
    while pgrep -f -- "--user-data-dir=$PROFILE" >/dev/null 2>&1 && [ "$n" -lt 10 ]; do
      sleep 1
      n=$((n + 1))
    done
    pkill -9 -f -- "--user-data-dir=$PROFILE" 2>/dev/null
  fi
  rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie"
}

# La fenêtre de Chromium existe-t-elle (elle est annoncée par le compositeur quand elle est affichée) ? yes | no | unknown
window_shown() {
  command -v wlrctl >/dev/null 2>&1 || { echo unknown; return; }
  windows=$(wlrctl toplevel list 2>/dev/null) || { echo unknown; return; }
  case $windows in
    *[Cc]hrom*) echo yes ;;
    *) echo no ;;
  esac
}

# Le dock voit-il le navigateur connecté ? yes | no | unknown (dock qui ne répond pas, ou qui ne le dit pas : on n'agit pas)
dock_sees_browser() {
  count=$(curl -fsS --noproxy '*' -m 3 "${URL}api/health" 2>/dev/null |
    sed -n 's/.*"browsers": *\([0-9][0-9]*\).*/\1/p')
  case $count in
    '') echo unknown ;;
    0) echo no ;;
    *) echo yes ;;
  esac
}

# yes : la page est à l'écran ; no : pas encore (ou pas du tout) ; unknown : on ne peut pas le savoir.
page_shown() {
  window=$(window_shown)
  seen=$(dock_sees_browser)
  if [ "$window" = no ] || [ "$seen" = no ]; then
    echo no
  elif [ "$window" = yes ] || [ "$seen" = yes ]; then
    echo yes
  else
    echo unknown
  fi
}

# Le processus $1 tourne-t-il encore ? (Un processus fini mais pas encore lu par « wait » répond à kill -0 : on lit son état.)
alive() {
  [ -d "/proc/$1" ] || return 1
  state=$(sed -n 's/^State:[[:space:]]*\(.\).*/\1/p' "/proc/$1/status" 2>/dev/null)
  [ -n "$state" ] && [ "$state" != Z ]
}

stop_chromium() {
  kill "$1" 2>/dev/null || return 0
  n=0
  while alive "$1" && [ "$n" -lt 10 ]; do
    sleep 1
    n=$((n + 1))
  done
  alive "$1" && kill -9 "$1" 2>/dev/null
  return 0
}

# Surveille Chromium (pid $1) jusqu'à ce que la page s'affiche ; s'il n'y arrive pas en SHOW_TIMEOUT secondes, l'arrête.
# Met shown à 1 quand la page est affichée.
watch_page() {
  shown=0
  waited=0
  while alive "$1"; do
    case $(page_shown) in
      yes)
        shown=1
        return
        ;;
      no)
        if [ "$waited" -ge "$SHOW_TIMEOUT" ]; then
          say "Chromium n'a rien affiché en ${SHOW_TIMEOUT} s (écran gris) : il est relancé"
          stop_chromium "$1"
          return
        fi
        waited=$((waited + WATCH_STEP))
        ;;
    esac
    sleep "$WATCH_STEP"
  done
}

failures=0
while true; do
  stop_orphans
  EXTRA=""
  if [ -r "$CONF_DIR/chromium.flags" ]; then
    EXTRA=$(grep -v '^[[:space:]]*#' "$CONF_DIR/chromium.flags" | tr '\n' ' ')
  fi
  if [ "$failures" -ge 3 ]; then
    say "trois lancements de suite sans page à l'écran : Chromium est relancé sans accélération graphique"
    EXTRA="$EXTRA --disable-gpu"
  fi
  # shellcheck disable=SC2086  # EXTRA contient volontairement plusieurs options
  "$CHROMIUM" --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --ozone-platform=wayland \
    --noerrdialogs --disable-infobars --no-first-run --no-default-browser-check \
    --disable-session-crashed-bubble --disable-translate \
    --disable-features=Translate,TranslateUI,MediaRouter \
    --disable-pinch --overscroll-history-navigation=0 \
    --disable-component-update --check-for-update-interval=31536000 \
    --password-store=basic --force-device-scale-factor=1 \
    --enable-gpu-rasterization $EXTRA &
  pid=$!
  watch_page "$pid"
  wait "$pid" 2>/dev/null
  if [ "$shown" -eq 1 ]; then failures=0; else failures=$((failures + 1)); fi
  if [ "$failures" -ge 3 ]; then sleep $((PAUSE * 2 + 1)); else sleep "$PAUSE"; fi
done
