#!/usr/bin/env bash
# Fonctions communes aux installateurs MyDock. À sourcer, pas à lancer.
# Écrit pour bash, sans option exotique des commandes de base (Ubuntu 26.04 utilise uutils).

DRY_RUN=${DRY_RUN:-0}
STAGING=${STAGING:-0}   # 1 : écrit dans un préfixe, sans apt, systemctl, chown (tests)

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=''; C_WARN=''; C_ERR=''; C_DIM=''; C_OFF=''
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s== %s ==%s\n' "$C_DIM" "$*" "$C_OFF"; }
ok()   { printf '%s✔%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die()  { printf '%s✘%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Exécute la commande, ou l'affiche seulement en mode simulation (--dry-run).
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s[simulation]%s %s\n' "$C_DIM" "$C_OFF" "$*"
    return 0
  fi
  "$@"
}

# Même chose avec les droits administrateur (sudo si on n'est pas déjà root).
as_root() {
  if [ "$(id -u)" = 0 ]; then run "$@"; else run sudo "$@"; fi
}

# put_file DESTINATION MODE [PROPRIETAIRE:GROUPE] < contenu
# Écrit le fichier seulement s'il change (contenu ou droits) et dit ce qu'il a fait (« + écrit » / « = inchangé »).
put_file() {
  local dest=$1 mode=$2 owner=${3:-} tmp
  tmp=$(mktemp)
  cat >"$tmp"
  if [ -f "$dest" ] && cmp -s "$tmp" "$dest" && [ "$(stat -c %a "$dest")" = "$mode" ]; then
    rm -f "$tmp"
        say "  = inchangé : $dest"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    rm -f "$tmp"
    say "  [simulation] écrirait : $dest"
    return 0
  fi
  install -D -m "$mode" "$tmp" "$dest"
  rm -f "$tmp"
  if [ -n "$owner" ] && [ "$STAGING" != 1 ]; then chown "$owner" "$dest"; fi
  say "  + écrit : $dest"
}

# Remplace les marqueurs @NOM@ d'un modèle : subst FICHIER NOM=valeur ...
# Les valeurs ne doivent pas contenir le caractère « | ».
subst() {
  local file=$1 expr=() pair
  shift
  for pair in "$@"; do
    expr+=(-e "s|@${pair%%=*}@|${pair#*=}|g")
  done
  sed "${expr[@]}" "$file"
}

# Ajoute les paquets manquants à la liste PKGS : need_cmd COMMANDE PAQUET
need_cmd() { have "$1" || PKGS+=("$2"); }

# Résout un nom en adresse IPv4 ; laisse passer adresses et réseaux.
resolve_ipv4() {
  case "$1" in
    *[!0-9./]*)
      local ip
      ip=$(getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}')
      [ -n "$ip" ] || die "Impossible de résoudre « $1 » (appareil éteint ou hors réseau ?). Donne son adresse IP à la place."
      printf '%s\n' "$ip"
      ;;
    *) printf '%s\n' "$1" ;;
  esac
}

# Adresse IPv4 de cette machine sur sa route par défaut (celle que le Pi doit utiliser).
lan_ip() {
  local address=""
  if have ip; then
    address=$(ip -4 route get 1.1.1.1 2>/dev/null |
      awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')
  fi
  [ -n "$address" ] || address=$(hostname -I 2>/dev/null | awk '{print $1}')
  printf '%s\n' "$address"
}

# Attend qu'un port local écoute : wait_port PORT [SECONDES]
wait_port() {
  local port=$1 tries=${2:-10} i
  for ((i = 0; i < tries * 4; i++)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then return 0; fi
    sleep 0.25
  done
  return 1
}

# Version majeure.mineure de l'interpréteur Python : python_mm CHEMIN
python_mm() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])'; }

require_python() {
  have "$1" || die "$1 est introuvable. Installe-le (sudo apt install python3 python3-venv)."
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
    die "Python 3.11 ou plus récent est requis (trouvé : $("$1" --version 2>&1))."
}

# Crée (ou recrée si Python a changé) le venv puis y installe les paquets : make_venv VENV PYTHON PAQUET...
make_venv() {
  local venv=$1 python=$2
  shift 2
  if [ -x "$venv/bin/python" ] && [ "$(python_mm "$venv/bin/python")" != "$(python_mm "$python")" ]; then
    say "  Python a changé ($(python_mm "$venv/bin/python") → $(python_mm "$python")) : le venv est recréé."
    run rm -rf "$venv"
  fi
  if [ ! -x "$venv/bin/python" ]; then
    run mkdir -p "$(dirname "$venv")"
    run "$python" -m venv "$venv"
  fi
  run "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
  run "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$@" ||
    die "L'installation des paquets Python a échoué. Si le message parle d'une roue (wheel) introuvable, ta version de Python est peut-être trop récente pour une dépendance : relance avec PYTHON=python3.13 (ou 3.12)."
}
