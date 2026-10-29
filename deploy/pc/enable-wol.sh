#!/usr/bin/env bash
# MyDock - prépare le PC à être allumé à distance (Wake-on-LAN) depuis le dock.
#
# Le réveil d'un PC ÉTEINT n'est possible qu'en Ethernet : la carte réseau doit rester à
# l'écoute quand la machine est arrêtée, et le BIOS doit l'autoriser. Ce script règle la partie
# système (NetworkManager, de façon durable) ; il dit ensuite ce qui reste à faire dans le BIOS.
#
# Usage : ./deploy/pc/enable-wol.sh [--wifi] [--dry-run]
#   --wifi  règle aussi le réveil par Wi-Fi (expérimental : depuis la veille seulement, selon la machine)
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib.sh
. "$HERE/../lib.sh"

WIFI=0
for arg in "$@"; do
  case "$arg" in
    --wifi) WIFI=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Option inconnue : $arg" ;;
  esac
done

have nmcli || die "NetworkManager (nmcli) est introuvable : ce script règle le réveil par NetworkManager."
have ethtool || die "ethtool est introuvable : sudo apt install ethtool"

step "Interfaces réseau physiques"
WIRED=(); WIRELESS=()
for path in /sys/class/net/*; do
  name=${path##*/}
  [ "$name" = lo ] && continue
  case "$(readlink -f "$path")" in */devices/virtual/*) continue ;; esac
  if [ -d "$path/wireless" ] || [ -d "$path/phy80211" ]; then WIRELESS+=("$name"); else WIRED+=("$name"); fi
done
say "  Ethernet : ${WIRED[*]:-aucune}"
say "  Wi-Fi    : ${WIRELESS[*]:-aucune}"
[ "${#WIRED[@]}" -gt 0 ] || warn "Aucune carte Ethernet : le réveil d'un PC éteint ne sera pas possible."

# Nom du profil NetworkManager lié à une interface (actif, sinon un profil Ethernet existant).
profile_for() {
  local dev=$1 type=$2 con
  con=$(nmcli -g GENERAL.CONNECTION device show "$dev" 2>/dev/null | head -n1)
  if [ -z "$con" ] || [ "$con" = "--" ]; then
    con=$(nmcli -t -f NAME,TYPE,DEVICE connection show 2>/dev/null |
      awk -F: -v t="$type" -v d="$dev" '$2 == t && ($3 == d || $3 == "") {print $1; exit}')
  fi
  printf '%s\n' "$con"
}

step "Ethernet"
for dev in "${WIRED[@]}"; do
  con=$(profile_for "$dev" 802-3-ethernet)
  if [ -z "$con" ]; then warn "$dev : aucun profil NetworkManager (branche le câble une fois pour qu'il en crée un)."; continue; fi
  current=$(nmcli -g 802-3-ethernet.wake-on-lan connection show "$con" 2>/dev/null || true)
  if [ "$current" = "magic" ] || [ "$current" = "64" ]; then
    ok "$dev ($con) : réveil « magic packet » déjà réglé"
  else
    say "  $dev ($con) : réglage du réveil (actuel : ${current:-?})"
    run nmcli connection modify "$con" 802-3-ethernet.wake-on-lan magic
    ok "$dev : le réglage sera appliqué à chaque connexion"
  fi
  # Application immédiate, sans attendre la prochaine connexion.
  as_root ethtool -s "$dev" wol g 2>/dev/null || warn "$dev : ethtool n'a pas pu appliquer le réglage maintenant (câble débranché ?)."
  state=$( (sudo -n ethtool "$dev" 2>/dev/null || ethtool "$dev" 2>/dev/null) | awk '/Wake-on:/ {print $2}' | tail -n1)
  say "  État actuel de la carte (ethtool) : ${state:-illisible sans sudo}   (g = réveil actif)"
done

if [ "$WIFI" = 1 ]; then
  step "Wi-Fi (expérimental)"
  for dev in "${WIRELESS[@]}"; do
    con=$(profile_for "$dev" 802-11-wireless)
    [ -n "$con" ] || { warn "$dev : aucun profil Wi-Fi actif."; continue; }
    run nmcli connection modify "$con" 802-11-wireless.wake-on-wlan magic
    ok "$dev ($con) : réveil par Wi-Fi demandé (aucune garantie : dépend du pilote et de la veille)."
  done
fi

step "À faire dans le BIOS / UEFI (une seule fois)"
cat <<'EOF'
  • Active « Wake on LAN » / « Power On by PCI-E » / « Wake on LAN from S5 ».
  • Désactive « ErP / EuP » ou « Deep Sleep » s'ils existent : ils coupent l'alimentation de la carte.
  • Portable : laisse-le branché sur le secteur (beaucoup de BIOS n'autorisent le réveil que sur secteur).
  • Le câble Ethernet doit être branché AVANT d'éteindre : sans lien réseau, la carte n'écoute pas.
  • Test : éteins le PC, attends 30 s, puis touche « Allumer le PC » sur le dock.
EOF
