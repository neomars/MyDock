#!/usr/bin/env bash
# MyDock - état du PC avant ou après l'installation (lecture seule, aucun secret affiché).
# Usage : ./deploy/pc/diagnose.sh [--anonyme]
#   --anonyme  masque les adresses MAC et les adresses IPv6 pour partager la sortie sans souci.
set -u

ANON=0
case "${1:-}" in
  --anonyme) ANON=1 ;;
  -h | --help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "Option inconnue : $1" >&2; exit 2 ;;
esac

report() {
  echo "== Système =="; grep PRETTY_NAME /etc/os-release; uname -r
  echo "== Session graphique =="
  echo "type=${XDG_SESSION_TYPE:-?} bureau=${XDG_CURRENT_DESKTOP:-?} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-}"
  gnome-shell --version 2>/dev/null
  systemctl --user show-environment 2>/dev/null |
    grep -E '^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS)=' |
    sed 's/=.*/ : défini dans systemd --user/'
  echo "== Python =="; python3 --version
  python3 -c 'import venv, ensurepip; print("venv + pip : ok")' 2>&1 | tail -1
  echo "== GPU =="; lspci 2>/dev/null | grep -iE 'vga|3d|display'
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null
  ls /sys/class/drm/card*/device/gpu_busy_percent 2>/dev/null
  echo "== Audio =="
  if command -v pactl >/dev/null; then
    LC_ALL=C pactl info 2>/dev/null | grep -E 'Server (Name|Version)|Default (Sink|Source)'
    LC_ALL=C pactl list short sinks 2>/dev/null | cut -f2,5
  else
    echo "pactl absent (paquet pulseaudio-utils) : l'installateur l'ajoutera"
  fi
  echo "== Lecteurs MPRIS actifs =="
  busctl --user list 2>/dev/null | awk '/org\.mpris\.MediaPlayer2/{print $1}'
  echo "== Steam =="
  local d
  for d in ~/.steam/steam ~/.local/share/Steam ~/snap/steam/common/.local/share/Steam \
    ~/.var/app/com.valvesoftware.Steam/.local/share/Steam; do
    [ -e "$d" ] && echo "dossier : $d"
  done
  dpkg -l 2>/dev/null | awk '$1=="ii" && $2 ~ /steam/ {print "deb :", $2, $3}'
  snap list steam 2>/dev/null | tail -n +2
  flatpak list --app 2>/dev/null | grep -i steam
  echo "== LibreOffice =="
  dpkg -l 2>/dev/null | awk '$1=="ii" && $2 ~ /^libreoffice-(writer|calc|impress)$/ {print "deb :", $2, $3}'
  snap list libreoffice 2>/dev/null | tail -n +2
  flatpak list --app 2>/dev/null | grep -i libreoffice
  command -v soffice
  echo "== Réseau et Wake-on-LAN =="; hostname; ip -br link 2>/dev/null | grep -v '^lo'
  local i path
  for path in /sys/class/net/*; do
    i=${path##*/}
    [ "$i" = lo ] && continue
    printf '%s : ' "$i"
    # « Wake-on » n'est lisible qu'en administrateur : on le tente sans mot de passe.
    (sudo -n ethtool "$i" 2>/dev/null || ethtool "$i" 2>/dev/null) | grep 'Wake-on' | tr -s ' \n' ' '
    echo
  done
  getent hosts "$(hostname).local" || echo "$(hostname).local : non résolu"
  systemctl is-active avahi-daemon 2>/dev/null
  echo "== Outils =="
  for c in gio gtk-launch systemd-run xdg-open ethtool nmcli ufw; do
    printf '%s : ' "$c"; command -v "$c" || echo absent
  done
  echo "== MyDock =="
  systemctl --user is-active mydock-agent 2>/dev/null || echo "agent non installé ou arrêté"
}

anonymise() {
  sed -E \
    -e 's/([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}/xx:xx:xx:xx:xx:xx/g' \
    -e 's/\b[23][0-9a-fA-F]{3}:[0-9a-fA-F:]+/IPv6-masquée/g'
}

if [ "$ANON" = 1 ]; then report 2>&1 | anonymise; else report 2>&1; fi
