#!/usr/bin/env bash
# MyDock - installation du dock sur le Raspberry Pi (serveur local + écran en kiosque).
# Idempotent : on peut le relancer après chaque « git pull ». À lancer avec sudo.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=deploy/lib.sh
. "$HERE/../lib.sh"

usage() {
  cat <<'EOF'
Usage : sudo ./deploy/pi/install-dock.sh --pc NOM_OU_IP [options]

  --pc HOTE          adresse IPv4 du PC piloté (recommandé : un nom .local échoue souvent depuis un Pi)
  --pc-port N        port de l'agent (défaut : 8765)
  --token-file F     fichier dont la première ligne est le jeton de l'agent.
                     Sans cette option : variable MYDOCK_TOKEN, sinon saisie masquée.
  --kiosk MODE       auto (défaut) | desktop | systemd | none
                       desktop : image Raspberry Pi OS Desktop (labwc) : le kiosque démarre avec
                                 la session ouverte automatiquement
                       systemd : image Lite (ou bureau désactivé) : labwc + Chromium sur la console
                       none    : seulement le serveur (écran géré à la main)
  --kiosk-user NOM   utilisateur de la session bureau (mode desktop ; sinon détecté)
  --force-mode WxH   force le mode de l'écran, ex. 1024x600 ou 1024x600@60
  --no-apt           n'installe aucun paquet
  --no-start         installe et active, sans démarrer maintenant
  --no-systemd       n'installe ni n'active aucun service (tests)
  --prefix DIR       écrit sous DIR au lieu de / (tests ; implique --no-apt --no-systemd)
  --dry-run          affiche ce qui serait fait, sans rien modifier
  -h, --help         cette aide

Variables : PYTHON (défaut python3), MYDOCK_TOKEN (jeton, évite la saisie).
EOF
}

PC=""; PC_PORT=""; TOKEN_FILE=""; KIOSK=auto; KIOSK_USER=""; FORCE_MODE=""
NO_APT=0; NO_START=0; NO_SYSTEMD=0; PREFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pc) [ $# -ge 2 ] || die "--pc attend une valeur"; PC=$2; shift 2 ;;
    --pc-port) [ $# -ge 2 ] || die "--pc-port attend une valeur"; PC_PORT=$2; shift 2 ;;
    --token-file) [ $# -ge 2 ] || die "--token-file attend une valeur"; TOKEN_FILE=$2; shift 2 ;;
    --kiosk) [ $# -ge 2 ] || die "--kiosk attend une valeur"; KIOSK=$2; shift 2 ;;
    --kiosk-user) [ $# -ge 2 ] || die "--kiosk-user attend une valeur"; KIOSK_USER=$2; shift 2 ;;
    --force-mode) [ $# -ge 2 ] || die "--force-mode attend une valeur"; FORCE_MODE=$2; shift 2 ;;
    --no-apt) NO_APT=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --prefix) [ $# -ge 2 ] || die "--prefix attend une valeur"; PREFIX=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "Option inconnue : $1" ;;
  esac
done

case "$KIOSK" in auto | desktop | systemd | none) ;; *) die "--kiosk : valeur inconnue « $KIOSK » (auto, desktop, systemd, none)" ;; esac
if [ -n "$FORCE_MODE" ] && ! printf '%s' "$FORCE_MODE" | grep -Eq '^[0-9]{3,5}x[0-9]{3,5}(@[0-9]{2,3})?$'; then
  die "--force-mode : format attendu 1024x600 ou 1024x600@60"
fi
if [ -n "$PREFIX" ]; then STAGING=1; NO_APT=1; NO_SYSTEMD=1; fi
if [ "$STAGING" != 1 ] && [ "$(id -u)" != 0 ]; then
  die "Lance ce script avec sudo : sudo ./deploy/pi/install-dock.sh --pc ..."
fi
[ "$DRY_RUN" = 1 ] && say "(simulation : rien ne sera modifié)"

PYTHON=${PYTHON:-python3}
OPT=$PREFIX/opt/mydock
SRC=$OPT/src
VENV=$OPT/venv
ETC=$PREFIX/etc/mydock
KETC=$PREFIX/etc/mydock-kiosk
STATE=$PREFIX/var/lib/mydock
UNIT_DIR=$PREFIX/etc/systemd/system
CONFIG=$ETC/dock.toml
# Chemins vus par les services (sans le préfixe de test).
R_SRC=/opt/mydock/src; R_VENV=/opt/mydock/venv; R_ETC=/etc/mydock; R_KETC=/etc/mydock-kiosk; R_STATE=/var/lib/mydock

# Copie un dossier sans les tests, caches et dépendances de développement (tar : toujours présent).
copy_tree() {
  local from=$1 to=$2
  if [ "$DRY_RUN" = 1 ]; then
    say "  [simulation] copierait $from → $to"
    return 0
  fi
  rm -rf "$to"
  mkdir -p "$to"
  tar -C "$from" --exclude='__pycache__' --exclude='*.egg-info' --exclude='tests' \
    --exclude='build' --exclude='node_modules' -cf - . | tar -C "$to" --no-same-owner -xf -
}

as_dock() { # exécute une commande sous l'utilisateur « mydock » (ou tel quel en test)
  if [ "$STAGING" = 1 ]; then run "$@"; else run runuser -u mydock -- "$@"; fi
}

# --- 1. Cet appareil ---------------------------------------------------------------------------
step "Cet appareil"
MODEL=""
[ -r /proc/device-tree/model ] && MODEL=$(tr -d '\0' </proc/device-tree/model)
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
fi
say "  Modèle  : ${MODEL:-inconnu (pas un Raspberry Pi ?)}"
say "  Système : ${PRETTY_NAME:-inconnu} ($(uname -m))"
case "$MODEL" in *Raspberry*) ;; *) warn "Ce n'est pas un Raspberry Pi : l'installation reste possible, mais elle a été pensée pour Raspberry Pi OS." ;; esac
require_python "$PYTHON"
ok "Python $(python_mm "$PYTHON")"
[ -f "$REPO/dock/web/dist/index.html" ] ||
  die "L'interface compilée est absente ($REPO/dock/web/dist). Elle est normalement fournie par le dépôt : fais « git pull », ou compile-la avec « make web » sur un PC."

# --- 2. Configuration demandée -----------------------------------------------------------------
step "PC piloté et jeton"
HAVE_CONFIG_TOKEN=0
if [ -f "$CONFIG" ] && grep -Eq '^token *= *"[^"]+"' "$CONFIG"; then HAVE_CONFIG_TOKEN=1; fi
HAVE_CONFIG_PC=0
if [ -f "$CONFIG" ] && grep -Eq '^host *= *"[^"]+"' "$CONFIG"; then HAVE_CONFIG_PC=1; fi
if [ -z "$PC" ] && [ "$HAVE_CONFIG_PC" = 0 ]; then
  usage >&2
  die "--pc est nécessaire à la première installation (adresse IPv4 du PC, ou nom .local)."
fi
TOKEN=""
if [ -n "$TOKEN_FILE" ]; then
  [ -r "$TOKEN_FILE" ] || die "Fichier de jeton illisible : $TOKEN_FILE"
  TOKEN=$(head -n1 "$TOKEN_FILE" | tr -d '\r\n ')
elif [ -n "${MYDOCK_TOKEN:-}" ]; then
  TOKEN=$MYDOCK_TOKEN
elif [ "$HAVE_CONFIG_TOKEN" = 0 ]; then
  if [ -t 0 ]; then
    read -r -s -p "  Jeton de l'agent (colle-le ici, la saisie est masquée) : " TOKEN
    echo
  fi
  [ -n "$TOKEN" ] || die "Il faut le jeton de l'agent. Sur le PC : mydock-agent show-token  (puis --token-file ou MYDOCK_TOKEN)."
fi
if [ -n "$PC" ]; then say "  PC : $PC"; fi
if [ -n "$TOKEN" ]; then say "  Jeton : fourni (non affiché)"; else say "  Jeton : celui de la configuration existante"; fi

# --- 3. Paquets système ------------------------------------------------------------------------
step "Paquets système"
PKGS=()
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null || PKGS+=(python3-venv)
need_cmd curl curl
if [ "$KIOSK" != none ]; then
  need_cmd labwc labwc
  need_cmd wlr-randr wlr-randr
  if ! have chromium && ! have chromium-browser; then PKGS+=(chromium); fi
  # Polices et pilotes graphiques Mesa (V3D/VC4) : indispensables à l'accélération GPU de Chromium.
  for pkg in fonts-noto-core fonts-noto-color-emoji libgl1-mesa-dri libegl1 libgles2; do
    dpkg -s "$pkg" >/dev/null 2>&1 || PKGS+=("$pkg")
  done
fi
for pkg in avahi-daemon libnss-mdns; do dpkg -s "$pkg" >/dev/null 2>&1 || PKGS+=("$pkg"); done
if [ "${#PKGS[@]}" -gt 0 ]; then
  if [ "$NO_APT" = 1 ]; then
    warn "Paquets manquants (--no-apt) : ${PKGS[*]}"
  else
    say "  Installation de : ${PKGS[*]}"
    as_root apt-get update -qq
    as_root apt-get install -y "${PKGS[@]}"
  fi
else
  ok "Tout est déjà installé"
fi

# --- 4. Utilisateurs système -------------------------------------------------------------------
step "Utilisateurs système"
if [ "$STAGING" = 1 ]; then
  say "  (test : aucun utilisateur créé)"
else
  # « mydock » tient le jeton ; « mydock-kiosk » fait tourner le navigateur : surfaces séparées.
  if ! getent passwd mydock >/dev/null; then
    run useradd --system --user-group --home-dir "$R_STATE" --no-create-home --shell /usr/sbin/nologin mydock
  fi
  ok "Utilisateur mydock (serveur, détient le jeton)"
  if [ "$KIOSK" = systemd ] || { [ "$KIOSK" = auto ] && ! systemctl is-enabled display-manager.service >/dev/null 2>&1; }; then
    if ! getent passwd mydock-kiosk >/dev/null; then
      run useradd --system --user-group --create-home --home-dir /var/lib/mydock-kiosk --shell /usr/sbin/nologin mydock-kiosk
    fi
    run usermod -aG video,render,input mydock-kiosk
    ok "Utilisateur mydock-kiosk (navigateur, sans accès au jeton)"
  fi
fi

# --- 5. Code, environnement Python -------------------------------------------------------------
step "Code et environnement Python ($OPT)"
run mkdir -p "$SRC" "$PREFIX/var/lib"
for item in common dock/server dock/web/dist deploy/pi; do
  copy_tree "$REPO/$item" "$SRC/$item"
done
run chmod +x "$SRC/deploy/pi/kiosk-browser.sh"
if [ "$DRY_RUN" = 1 ]; then say "  (simulation : sources non copiées)"; else ok "Sources copiées dans $SRC"; fi
make_venv "$VENV" "$PYTHON" "$SRC/common" "$SRC/dock/server"
[ "$DRY_RUN" = 1 ] || ok "Serveur installé : $("$VENV/bin/mydock-dock" --version)"

# --- 6. Configuration du dock (droits 600) -----------------------------------------------------
step "Configuration ($CONFIG)"
if [ "$STAGING" = 1 ]; then
  run install -d -m 750 "$ETC" "$STATE"
else
  run install -d -m 750 -o mydock -g mydock "$ETC" "$STATE"
fi
INIT=("$VENV/bin/mydock-dock" init --config "$CONFIG" --state-dir "$R_STATE" --web-dir "$R_SRC/dock/web/dist")
[ -n "$PC" ] && INIT+=(--pc "$PC")
[ -n "$PC_PORT" ] && INIT+=(--pc-port "$PC_PORT")
if [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] ${INIT[*]} ${TOKEN:+--token-stdin}"
elif [ -n "$TOKEN" ]; then
  printf '%s\n' "$TOKEN" | as_dock "${INIT[@]}" --token-stdin | sed 's/^/  /'
else
  as_dock "${INIT[@]}" | sed 's/^/  /'
fi
if [ "$STAGING" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  [ "$(stat -c %a "$CONFIG")" = 600 ] || die "La configuration n'est pas en droits 600 : $CONFIG"
fi

# --- 7. Service du dock ------------------------------------------------------------------------
step "Service mydock-dock"
subst "$HERE/mydock-dock.service" "VENV=$R_VENV" "ETC=$R_ETC" "STATE=$R_STATE" |
  put_file "$UNIT_DIR/mydock-dock.service" 644
if [ "$NO_SYSTEMD" = 1 ]; then
  warn "Services non activés (--no-systemd / --prefix)."
elif [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] systemctl daemon-reload && enable mydock-dock && restart mydock-dock"
else
  systemctl daemon-reload
  systemctl enable mydock-dock.service >/dev/null 2>&1
  if [ "$NO_START" = 0 ]; then
    systemctl restart mydock-dock.service
    if wait_port 8080 15; then ok "Le serveur du dock répond sur 127.0.0.1:8080"; else warn "Le serveur ne répond pas : journalctl -u mydock-dock -e"; fi
  fi
fi

# --- 8. Écran en kiosque -----------------------------------------------------------------------
step "Écran en kiosque"
if [ "$KIOSK" = auto ]; then
  if have systemctl && systemctl is-enabled display-manager.service >/dev/null 2>&1; then KIOSK=desktop; else KIOSK=systemd; fi
  say "  Mode choisi automatiquement : $KIOSK"
fi

if [ "$KIOSK" != none ]; then
  run install -d -m 755 "$KETC"
  if [ -n "$FORCE_MODE" ]; then
    printf '# Géré par MyDock : mode forcé de l'"'"'écran.\nMYDOCK_OUTPUT_MODE="%s"\n' "$FORCE_MODE" |
      put_file "$KETC/display.conf" 644
  elif [ ! -e "$KETC/display.conf" ]; then
    printf '# MYDOCK_OUTPUT_MODE="1024x600@60"   # décommente pour forcer le mode de l'"'"'écran\n' |
      put_file "$KETC/display.conf" 644
  fi
  if [ ! -e "$KETC/chromium.flags" ]; then
    printf '# Options supplémentaires de Chromium, une par ligne (voir « dépannage » du README).\n# --use-gl=angle\n' |
      put_file "$KETC/chromium.flags" 644
  fi
  subst "$HERE/labwc/autostart" "SRC=$R_SRC" | put_file "$KETC/labwc/autostart" 644
fi

case "$KIOSK" in
  desktop)
    USER_NAME=$KIOSK_USER
    if [ -z "$USER_NAME" ]; then
      USER_NAME=$(grep -rhs '^autologin-user=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/ 2>/dev/null | tail -n1 | cut -d= -f2 || true)
    fi
    if [ -z "$USER_NAME" ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then USER_NAME=$SUDO_USER; fi
    [ -n "$USER_NAME" ] || die "Utilisateur de la session bureau introuvable : ajoute --kiosk-user NOM."
    if [ "$STAGING" = 1 ]; then USER_HOME=$PREFIX/home/$USER_NAME; else USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6); fi
    [ -n "$USER_HOME" ] || die "Utilisateur inconnu : $USER_NAME"
    say "  Session bureau : $USER_NAME (labwc)"
    AUTOSTART=$USER_HOME/.config/labwc/autostart
    if [ -f "$AUTOSTART" ] && ! grep -q 'MyDock' "$AUTOSTART" && [ ! -e "$AUTOSTART.avant-mydock" ]; then
      run cp -p "$AUTOSTART" "$AUTOSTART.avant-mydock"
      say "  Ancien autostart sauvegardé : $AUTOSTART.avant-mydock"
    fi
    if [ "$STAGING" = 1 ]; then
      run install -d -m 755 "$USER_HOME/.config/labwc"
    else
      run install -d -m 755 -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$USER_HOME/.config" "$USER_HOME/.config/labwc"
    fi
    subst "$HERE/labwc/autostart" "SRC=$R_SRC" | put_file "$AUTOSTART" 644 "$USER_NAME:"
    if grep -rqhsE '(autologin-session|user-session)=(rpd-x|LXDE|wayfire)' /etc/lightdm/ 2>/dev/null; then
      warn "La session du bureau n'est pas labwc (X11 ou Wayfire) : le kiosque ne démarrera pas. Passe en labwc : sudo raspi-config → Advanced Options → Wayland → labwc."
    fi
    ok "Le kiosque démarrera à la prochaine ouverture de session ($USER_NAME) : sudo reboot"
    ;;
  systemd)
    subst "$HERE/mydock-kiosk.service" "ETC=$R_KETC" | put_file "$UNIT_DIR/mydock-kiosk.service" 644
    # L'unité attend --config-dir <dossier>/labwc : même arborescence que le dossier de configuration public.
    if [ "$NO_SYSTEMD" = 1 ]; then
      warn "Service du kiosque non activé (--no-systemd / --prefix)."
    elif [ "$DRY_RUN" = 1 ]; then
      say "  [simulation] systemctl enable mydock-kiosk (et désactivation du gestionnaire de session si présent)"
    else
      if systemctl is-enabled display-manager.service >/dev/null 2>&1; then
        warn "Un gestionnaire de session graphique est actif : il est désactivé pour laisser la console au kiosque."
        say "  Pour revenir en arrière : sudo systemctl enable lightdm && sudo systemctl set-default graphical.target"
        systemctl disable display-manager.service >/dev/null 2>&1 || true
      fi
      systemctl set-default multi-user.target >/dev/null 2>&1
      systemctl daemon-reload
      systemctl enable mydock-kiosk.service >/dev/null 2>&1
      if [ "$NO_START" = 0 ]; then systemctl restart mydock-kiosk.service; ok "Kiosque démarré sur la console 1"; fi
    fi
    ;;
  none) say "  Aucun kiosque installé (--kiosk none)." ;;
esac

# --- Fin ---------------------------------------------------------------------------------------
step "Terminé"
if [ "$DRY_RUN" = 1 ]; then
  say "Simulation terminée : relance sans --dry-run pour installer."
  exit 0
fi
if [ -n "$PC" ] && ! getent ahostsv4 "$PC" >/dev/null 2>&1; then
  warn "Le nom « $PC » ne se résout pas pour l'instant (PC éteint ? mDNS ?). Ce n'est pas bloquant : le dock réessaiera tout seul. Tu peux aussi saisir l'adresse IP du PC dans Réglages."
fi
ok "Dock installé."
say ""
say "Diagnostic : $VENV/bin/mydock-dock doctor --config $R_ETC/dock.toml   (en root : sudo -u mydock …)"
say "Journaux   : journalctl -u mydock-dock -f"
say "Mise à jour : cd $(basename "$REPO") && git pull && sudo ./deploy/pi/install-dock.sh"
