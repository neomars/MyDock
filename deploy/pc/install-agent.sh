#!/usr/bin/env bash
# MyDock - installation de l'agent sur le PC SANS paquet (environnement Python dédié, service
# systemd *utilisateur*). Sur Ubuntu, préfère le paquet .deb : voir README (« mydock-agent setup »).
# Idempotent : on peut le relancer après chaque « git pull » ; le jeton existant est conservé.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=deploy/lib.sh
. "$HERE/../lib.sh"

usage() {
  cat <<'EOF'
Usage : ./deploy/pc/install-agent.sh [options]

  --pi ADRESSE      Pi autorisé à se connecter : adresse IPv4, réseau (192.168.1.0/24) ou nom.
                    Répétable. Recommandé : l'adresse IP du Pi seul.
  --port N          port d'écoute (défaut : 8765)
  --bind ADRESSE    adresse d'écoute IPv4 (défaut : 0.0.0.0)
  --rotate-token    génère un nouveau jeton (le Pi devra le reprendre)
  --no-apt          n'installe aucun paquet système
  --no-firewall     ne touche pas à ufw
  --no-systemd      n'installe ni ne démarre le service (tests)
  --dry-run         affiche ce qui serait fait, sans rien modifier
  -h, --help        cette aide

Variables : PYTHON (interpréteur à utiliser, défaut python3), MYDOCK_HOME (défaut ~/.local/share/mydock).
EOF
}

CLIENTS=(); PORT=""; BIND=""; ROTATE=0; NO_APT=0; NO_FW=0; NO_SYSTEMD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pi) [ $# -ge 2 ] || die "--pi attend une valeur"; CLIENTS+=("$2"); shift 2 ;;
    --port) [ $# -ge 2 ] || die "--port attend une valeur"; PORT=$2; shift 2 ;;
    --bind) [ $# -ge 2 ] || die "--bind attend une valeur"; BIND=$2; shift 2 ;;
    --rotate-token) ROTATE=1; shift ;;
    --no-apt) NO_APT=1; shift ;;
    --no-firewall) NO_FW=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "Option inconnue : $1" ;;
  esac
done

PYTHON=${PYTHON:-python3}
MYDOCK_HOME=${MYDOCK_HOME:-$HOME/.local/share/mydock}
CONFIG_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/mydock
CONFIG=$CONFIG_DIR/agent.toml
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
VENV=$MYDOCK_HOME/venv
UNIT_NAME=mydock-agent.service

if [ "$(id -u)" = 0 ] && [ "${MYDOCK_ALLOW_ROOT:-0}" != 1 ]; then
  die "Ne lance pas ce script avec sudo : l'agent est un service de ta session (il doit voir ton écran, ton son, tes applications). Lance-le en simple utilisateur ; sudo sera demandé seulement quand il faut."
fi
if have dpkg-query && dpkg-query -W -f='${Status}' mydock-agent 2>/dev/null | grep -q 'install ok installed'; then
  die "Le paquet mydock-agent est installé : configure-le avec « mydock-agent setup --pi ADRESSE_IP_DU_PI ». Ce script sert à une installation sans paquet ; les deux ensemble se gêneraient (pour passer à ce script : sudo apt remove mydock-agent)."
fi
[ "$DRY_RUN" = 1 ] && say "(simulation : rien ne sera modifié)"

# --- 1. Ce PC ----------------------------------------------------------------------------------
step "Ce PC"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  say "  Système : ${PRETTY_NAME:-inconnu}"
fi
say "  Session : ${XDG_SESSION_TYPE:-inconnue} (${XDG_CURRENT_DESKTOP:-bureau inconnu})"
case "${XDG_SESSION_TYPE:-}" in
  wayland | x11) ;;
  *) warn "Aucune session graphique détectée dans ce terminal : lance le script depuis un terminal de ta session (pas en SSH) pour que le service voie l'écran." ;;
esac
require_python "$PYTHON"
ok "Python $(python_mm "$PYTHON") ($PYTHON)"

# --- 2. Paquets système ------------------------------------------------------------------------
step "Paquets système"
PKGS=()
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null || PKGS+=(python3-venv)
need_cmd pactl pulseaudio-utils   # son (fonctionne avec PipeWire)
need_cmd gio libglib2.0-bin       # lancement d'applications
need_cmd ethtool ethtool          # lecture du Wake-on-LAN
if [ "${#PKGS[@]}" -gt 0 ]; then
  if [ "$NO_APT" = 1 ]; then
    warn "Paquets manquants (--no-apt) : ${PKGS[*]}"
  else
    say "  Installation de : ${PKGS[*]}"
    as_root apt-get install -y --no-install-recommends "${PKGS[@]}"
  fi
else
  ok "Tout est déjà installé"
fi

# --- 3. Environnement Python et agent ----------------------------------------------------------
step "Agent MyDock (environnement Python)"
make_venv "$VENV" "$PYTHON" "$REPO/common" "$REPO/agent"
if [ "$DRY_RUN" != 1 ]; then
  ok "Agent installé : $("$VENV/bin/mydock-agent" --version)"
fi

# --- 4. Configuration et jeton -----------------------------------------------------------------
step "Configuration (droits 600) et jeton"
INIT_ARGS=()
RESOLVED=()
for client in "${CLIENTS[@]}"; do
  RESOLVED+=("$(resolve_ipv4 "$client")")
done
for client in "${RESOLVED[@]}"; do INIT_ARGS+=(--allow "$client"); done
[ -n "$PORT" ] && INIT_ARGS+=(--port "$PORT")
[ -n "$BIND" ] && INIT_ARGS+=(--bind "$BIND")
[ "$ROTATE" = 1 ] && INIT_ARGS+=(--rotate-token)
if [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] mydock-agent init --config $CONFIG ${INIT_ARGS[*]:-}"
  TOKEN_NEW=0
else
  run mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  INIT_OUT=$("$VENV/bin/mydock-agent" init --config "$CONFIG" "${INIT_ARGS[@]}")
  say "$INIT_OUT" | sed 's/^/  /'
  case "$INIT_OUT" in *"nouveau jeton"*) TOKEN_NEW=1 ;; *) TOKEN_NEW=0 ;; esac
fi
if [ "${#RESOLVED[@]}" -eq 0 ]; then
  warn "Aucun --pi donné : seuls les réseaux locaux de ce PC sont acceptés. Pour limiter l'accès au Pi : relance avec --pi ADRESSE_IP_DU_PI."
fi

# --- 5. Service systemd utilisateur ------------------------------------------------------------
step "Service systemd utilisateur ($UNIT_NAME)"
subst "$HERE/mydock-agent.service" "VENV=$VENV" | put_file "$UNIT_DIR/$UNIT_NAME" 644
if [ "$NO_SYSTEMD" = 1 ]; then
  warn "--no-systemd : le service n'est ni activé ni démarré."
elif [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] systemctl --user daemon-reload && enable && restart $UNIT_NAME"
else
  systemctl --user show-environment >/dev/null 2>&1 ||
    die "systemd utilisateur est injoignable depuis ce terminal. Ouvre un terminal dans ta session graphique (ou fais « loginctl enable-linger \$USER » puis reconnecte-toi)."
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT_NAME" >/dev/null 2>&1
  # Le code vient d'être réinstallé : on redémarre pour l'appliquer (coupure d'une seconde).
  systemctl --user restart "$UNIT_NAME"
  PORT_NOW=$(grep -E '^port *=' "$CONFIG" | head -n1 | sed 's/[^0-9]//g')
  if wait_port "${PORT_NOW:-8765}" 12; then
    ok "L'agent écoute sur le port ${PORT_NOW:-8765}"
  else
    warn "L'agent ne répond pas encore. Regarde : journalctl --user -u $UNIT_NAME -e"
  fi
fi

# --- 6. Pare-feu -------------------------------------------------------------------------------
step "Pare-feu"
PORT_FW=${PORT:-8765}
if [ "$NO_FW" = 1 ] || [ "$NO_SYSTEMD" = 1 ]; then
  say "  ignoré"
elif ! have ufw; then
  say "  ufw n'est pas installé : rien à faire."
elif [ "$DRY_RUN" = 1 ]; then
  say "  [simulation] ufw allow from <pi> to any port $PORT_FW"
elif ! as_root ufw status 2>/dev/null | grep -q '^Status: active'; then
  say "  ufw est inactif : rien à faire."
elif [ "${#RESOLVED[@]}" -eq 0 ]; then
  warn "ufw est actif mais aucun --pi n'est donné : le Pi sera bloqué. Relance avec --pi ADRESSE_IP_DU_PI."
else
  for client in "${RESOLVED[@]}"; do
    as_root ufw allow from "$client" to any port "$PORT_FW" proto tcp comment 'MyDock agent' >/dev/null
    ok "ufw : $client autorisé sur le port $PORT_FW"
  done
fi

# --- Fin ---------------------------------------------------------------------------------------
HOST_NAME=$(hostname)
step "Terminé"
if [ "$DRY_RUN" = 1 ]; then
  say "Simulation terminée : relance sans --dry-run pour installer."
  exit 0
fi
ok "Agent installé."
say ""
if [ "$TOKEN_NEW" = 1 ]; then
  say "Jeton à saisir sur le Pi (il n'est affiché que maintenant ; ne le publie jamais) :"
  say ""
  say "    $("$VENV/bin/mydock-agent" show-token)"
else
  say "Jeton existant conservé. Pour l'afficher à nouveau : $VENV/bin/mydock-agent show-token"
fi
say ""
PC_IP=$(lan_ip)
say "Sur le Pi (dans ce dépôt), avec l'adresse IP de ce PC :"
say "    sudo ./deploy/pi/install-dock.sh --pc ${PC_IP:-${HOST_NAME}.local}"
say "Préfère l'adresse IP au nom ${HOST_NAME}.local : la résolution des noms .local échoue souvent depuis un Pi."
say "Réserve cette adresse dans ta box (bail DHCP) pour qu'elle ne change pas."
say "Vérifier depuis le Pi (404 = l'agent est joint) :"
say "    curl -sS -m 5 -o /dev/null -w '%{http_code}\\n' http://${PC_IP:-ADRESSE_DU_PC}:${PORT_NOW:-${PORT:-8765}}/"
say ""
say "Diagnostic : $VENV/bin/mydock-agent doctor"
say "Pour pouvoir allumer ce PC depuis le dock : ./deploy/pc/enable-wol.sh"
