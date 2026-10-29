#!/bin/sh
# MyDock - lance Chromium en kiosque sur l'interface du dock, et le relance s'il se ferme.
# Lancé par labwc (autostart), sous Wayland. Réglages optionnels :
#   /etc/mydock-kiosk/display.conf   MYDOCK_OUTPUT_MODE="1024x600@60"   (force le mode de l'écran)
#   /etc/mydock-kiosk/chromium.flags options supplémentaires de Chromium, une par ligne (# = commentaire)
PORT="${MYDOCK_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/"
CONF_DIR="${MYDOCK_CONF_DIR:-/etc/mydock-kiosk}"
PROFILE="${MYDOCK_PROFILE:-${XDG_CACHE_HOME:-$HOME/.cache}/mydock-chromium}"

for candidate in chromium chromium-browser; do
  if command -v "$candidate" >/dev/null 2>&1; then CHROMIUM=$candidate; break; fi
done
[ -n "${CHROMIUM:-}" ] || { echo "MyDock : Chromium est introuvable (sudo apt install chromium)" >&2; exit 1; }

# Mode de l'écran (le Waveshare 7" 1024x600 est normalement reconnu tout seul).
if [ -r "$CONF_DIR/display.conf" ]; then
  # shellcheck disable=SC1091
  . "$CONF_DIR/display.conf"
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

while true; do
  EXTRA=""
  if [ -r "$CONF_DIR/chromium.flags" ]; then
    EXTRA=$(grep -v '^[[:space:]]*#' "$CONF_DIR/chromium.flags" | tr '\n' ' ')
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
    --enable-gpu-rasterization $EXTRA
  sleep 2
done
