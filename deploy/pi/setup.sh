#!/usr/bin/env bash
# MyDock - intégration système du dock (Raspberry Pi), lancée par le paquet « mydock » à
# l'installation et à chaque mise à jour. On ne la lance à la main que pour changer de kiosque :
#     sudo /usr/lib/mydock/setup --kiosk systemd
# Idempotente : crée la configuration (droits 600), branche les services, installe le kiosque
# (Chromium plein écran dans labwc) et la veille de l'écran. Elle ne demande jamais rien.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -f "$HERE/lib.sh" ]; then LIB=$HERE/lib.sh; else LIB=$HERE/../lib.sh; fi
# shellcheck source=deploy/lib.sh
. "$LIB"

usage() {
  cat <<'EOF'
Usage : sudo /usr/lib/mydock/setup [options]

  --kiosk MODE       auto (défaut) | desktop | systemd | none
                       desktop : image Raspberry Pi OS Desktop (labwc) : le kiosque démarre avec
                                 la session ouverte automatiquement
                       systemd : image Lite (ou bureau désactivé) : labwc + Chromium sur la console
                       none    : seulement le serveur (écran géré à la main)
  --kiosk-user NOM   utilisateur de la session bureau (mode desktop ; sinon détecté)
  --force-mode WxH   force le mode de l'écran, ex. 1024x600 ou 1024x600@60
  --no-start         installe et active, sans démarrer maintenant
  --remove           retire le kiosque et arrête les services (appelé par le paquet à sa suppression)
  --prefix DIR       écrit sous DIR au lieu de / (tests ; implique aucun appel à systemd)
  --dry-run          affiche ce qui serait fait, sans rien modifier
  -h, --help         cette aide

Variable : MYDOCK_DOCK_BIN (défaut /usr/bin/mydock-dock).
EOF
}

KIOSK=auto; KIOSK_USER=""; FORCE_MODE=""; NO_START=0; REMOVE=0; PREFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --kiosk) [ $# -ge 2 ] || die "--kiosk attend une valeur"; KIOSK=$2; shift 2 ;;
    --kiosk-user) [ $# -ge 2 ] || die "--kiosk-user attend une valeur"; KIOSK_USER=$2; shift 2 ;;
    --force-mode) [ $# -ge 2 ] || die "--force-mode attend une valeur"; FORCE_MODE=$2; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    --remove) REMOVE=1; shift ;;
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
NO_SYSTEMD=0
if [ -n "$PREFIX" ]; then STAGING=1; NO_SYSTEMD=1; fi
if [ "$STAGING" != 1 ] && [ "$(id -u)" != 0 ]; then
  die "Cette étape se lance avec sudo (le paquet s'en charge à l'installation)."
fi
[ "$DRY_RUN" = 1 ] && say "(simulation : rien ne sera modifié)"

DOCK=${MYDOCK_DOCK_BIN:-/usr/bin/mydock-dock}
ETC=$PREFIX/etc/mydock
KETC=$PREFIX/etc/mydock-kiosk
STATE=$PREFIX/var/lib/mydock
LOGDIR=$PREFIX/var/log/mydock
UNIT_DIR=$PREFIX/etc/systemd/system
CONFIG=$ETC/dock.toml
# Chemins vus par les services (sans le préfixe de test).
R_LIB=/usr/lib/mydock; R_ETC=/etc/mydock; R_KETC=/etc/mydock-kiosk; R_STATE=/var/lib/mydock
R_WEB=/usr/share/mydock/web; R_CLIENTS=/usr/share/mydock/clients
R_LOG=/var/log/mydock

as_dock() { # exécute une commande sous l'utilisateur « mydock » (ou tel quel en test)
  if [ "$STAGING" = 1 ]; then run "$@"; else run runuser -u mydock -- "$@"; fi
}

# --- retrait : appelé par le paquet avant sa suppression ---------------------------------------
if [ "$REMOVE" = 1 ]; then
  step "Services"
  if [ "$NO_SYSTEMD" != 1 ]; then
    for unit in mydock-kiosk mydock-dock; do
      run systemctl disable --now "$unit.service" >/dev/null 2>&1 || true
    done
  fi
  run rm -f "$UNIT_DIR/mydock-kiosk.service"
  # Un systemd injoignable (conteneur, chroot) ne doit pas empêcher de rendre le bureau : la suite se fait quand même.
  [ "$NO_SYSTEMD" = 1 ] || run systemctl daemon-reload || true
  ok "Services arrêtés"

  step "Kiosque en mode bureau"
  for home in "$PREFIX"/home/*; do
    [ -d "$home" ] || continue
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
  run rm -rf "$KETC"
  say "Configuration (/etc/mydock) et état (/var/lib/mydock) conservés ; « apt purge mydock » les supprime."
  say "Si tu avais désactivé le gestionnaire de session : sudo systemctl enable lightdm && sudo systemctl set-default graphical.target"
  exit 0
fi

# --- 1. Configuration du dock (droits 600) -----------------------------------------------------
step "Configuration ($R_ETC/dock.toml)"
if [ "$STAGING" = 1 ]; then
  run install -d -m 750 "$ETC" "$STATE"
else
  run install -d -m 750 -o mydock -g mydock "$ETC" "$STATE"
fi
# Sans « --pc » : un dock neuf n'a pas de PC, il affiche un code que le PC saisit (portail). Sur un
# dock qui en a déjà un, init ne change que les chemins (l'ancien installateur les mettait ailleurs).
INIT=("$DOCK" init --config "$CONFIG" --state-dir "$R_STATE" --web-dir "$R_WEB" --clients-dir "$R_CLIENTS")
if [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] ${INIT[*]}"
else
  as_dock "${INIT[@]}" | sed 's/^/  /'
fi
if [ "$STAGING" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  [ "$(stat -c %a "$CONFIG")" = 600 ] || die "La configuration n'est pas en droits 600 : $CONFIG"
fi

# --- 1b. Journaux du Divertissement (/var/log/mydock) ------------------------------------------------------------
# player.log (l'assistant de lecture, qui tourne sous l'utilisateur de la session de l'écran) et dock.log (le serveur,
# sous « mydock ») : un dossier ouvert à tous en écriture (1777, comme /tmp : chacun n'efface que ses fichiers). Ils ne
# contiennent jamais de mot de passe ; ils servent à comprendre pourquoi une vidéo ne se lance pas.
step "Journaux ($R_LOG)"
run install -d -m 1777 "$LOGDIR"
ok "Journaux : $R_LOG/player.log (lecture) et $R_LOG/dock.log (serveur)"

# --- 2. Ancienne installation (script install-dock.sh, environnement dans /opt/mydock) ---------
# Son unité, dans /etc/systemd/system, masquerait celle du paquet : on la retire.
migrated=0
unit=$UNIT_DIR/mydock-dock.service
if [ -f "$unit" ] && grep -q '/opt/mydock/venv' "$unit"; then
  step "Ancienne installation"
  [ "$NO_SYSTEMD" = 1 ] || run systemctl disable --now mydock-dock.service >/dev/null 2>&1 || true
  run rm -f "$unit"
  migrated=1
  ok "Ancienne unité mydock-dock retirée : celle du paquet la remplace"
  say "  L'ancien dossier /opt/mydock ne sert plus : sudo rm -r /opt/mydock"
fi

# --- 3. Service du dock ------------------------------------------------------------------------
step "Service mydock-dock"
if [ "$NO_SYSTEMD" = 1 ]; then
  warn "Services non activés (--prefix)."
elif [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] systemctl daemon-reload && enable mydock-dock && restart mydock-dock"
else
  systemctl daemon-reload
  systemctl enable mydock-dock.service >/dev/null 2>&1
  if [ "$NO_START" = 0 ]; then
    systemctl restart mydock-dock.service
    if wait_port 8080 15; then ok "Le serveur du dock répond sur 127.0.0.1:8080"; else warn "Le serveur ne répond pas : journalctl -u mydock-dock -e"; fi
    if curl -fsS --noproxy '*' -m 3 http://127.0.0.1/api/portal >/dev/null 2>&1; then
      ok "Le portail répond sur le port 80 : un PC peut s'installer depuis ce Pi"
    else
      warn "Le portail ne répond pas sur le port 80 : journalctl -u mydock-dock -e"
    fi
  fi
fi

# --- 4. Utilisateurs système du kiosque --------------------------------------------------------
step "Écran en kiosque"
if [ "$KIOSK" = auto ]; then
  if have systemctl && systemctl is-enabled display-manager.service >/dev/null 2>&1; then KIOSK=desktop; else KIOSK=systemd; fi
  say "  Mode choisi automatiquement : $KIOSK"
fi

if [ "$KIOSK" = systemd ] && [ "$STAGING" != 1 ]; then
  # « mydock » tient le jeton ; « mydock-kiosk » fait tourner le navigateur : surfaces séparées.
  if ! getent passwd mydock-kiosk >/dev/null; then
    run useradd --system --user-group --create-home --home-dir /var/lib/mydock-kiosk --shell /usr/sbin/nologin mydock-kiosk
  fi
  # « audio » : le son des vidéos du Divertissement (VLC) sort par la carte son du Pi (HDMI ou prise).
  run usermod -aG video,render,input,audio mydock-kiosk
  ok "Utilisateur mydock-kiosk (navigateur, sans accès au jeton)"
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
  if [ ! -e "$KETC/vlc.flags" ]; then
    printf '# Options supplémentaires de VLC pour le Divertissement, une par ligne (voir « dépannage » du README).\n# --aout=alsa\n# --avcodec-hw=none --deinterlace=1   (désentrelacer par le processeur : chaînes en SD)\n' |
      put_file "$KETC/vlc.flags" 644
  fi
  subst "$HERE/labwc/autostart" "LIB=$R_LIB" | put_file "$KETC/labwc/autostart" 644
fi

case "$KIOSK" in
  desktop)
    USER_NAME=$KIOSK_USER
    if [ -z "$USER_NAME" ]; then
      USER_NAME=$(grep -rhs '^autologin-user=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/ 2>/dev/null | tail -n1 | cut -d= -f2 || true)
    fi
    if [ -z "$USER_NAME" ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then USER_NAME=$SUDO_USER; fi
    if [ -z "$USER_NAME" ]; then
      # Un paquet s'installe parfois sans « sudo » (apt en root, SSH) : on cherche l'utilisateur humain.
      USER_NAME=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ {print $1; exit}')
    fi
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
    subst "$HERE/labwc/autostart" "LIB=$R_LIB" | put_file "$AUTOSTART" 644 "$USER_NAME:"
    if grep -rqhsE '(autologin-session|user-session)=(rpd-x|LXDE|wayfire)' /etc/lightdm/ 2>/dev/null; then
      warn "La session du bureau n'est pas labwc (X11 ou Wayfire) : le kiosque ne démarrera pas. Passe en labwc : sudo raspi-config → Advanced Options → Wayland → labwc."
    fi
    if [ "$STAGING" != 1 ] && [ "$DRY_RUN" != 1 ] && [ "$NO_START" = 0 ]; then
      # Mise à jour : le navigateur du kiosque se relance tout seul (le lanceur boucle) sur la nouvelle interface.
      pkill -u "$USER_NAME" -x chromium >/dev/null 2>&1 || true
      # L'assistant de lecture aussi : son lanceur le relance sur la nouvelle version.
      pkill -u "$USER_NAME" -f 'mydock-dock player-session' >/dev/null 2>&1 || true
    fi
    ok "Le kiosque et le Divertissement démarreront à la prochaine ouverture de session ($USER_NAME) : sudo reboot"
    ;;
  systemd)
    subst "$HERE/mydock-kiosk.service" "ETC=$R_KETC" | put_file "$UNIT_DIR/mydock-kiosk.service" 644
    # L'unité attend --config-dir <dossier>/labwc : même arborescence que le dossier de configuration public.
    if [ "$NO_SYSTEMD" = 1 ]; then
      warn "Service du kiosque non activé (--prefix)."
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
[ "$migrated" = 1 ] && say "(l'ancienne installation a été remplacée : la configuration et le jeton sont conservés)"
address=$(lan_ip)
ok "Dock installé."
say ""
say "Pour installer ton PC, depuis le PC :"
say "    ouvre   http://${address:-ADRESSE_DU_PI}/   (ou http://$(hostname).local/)"
say "    puis télécharge le paquet, installe-le et associe-le : la page dit quoi taper,"
say "    le code à saisir est affiché sur l'écran du dock."
say ""
say "Diagnostic : sudo -u mydock mydock-dock doctor"
say "Journaux   : journalctl -u mydock-dock -f"
