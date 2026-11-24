#!/usr/bin/env bash
# pihole.sh — set up, test and look after Pi-hole on this Pi (README.md here).
#
# First-time setup, one step at a time, in this order (each is re-runnable):
#   ./pihole.sh check              what's here: OS, boot storage, port 53, Docker (changes nothing)
#   ./pihole.sh install-docker     install Docker (asks first)
#   ./pihole.sh host-dns           the Pi itself uses 1.1.1.1 / 9.9.9.9, never Pi-hole; Wi-Fi power saving off (asks first)
#   ./pihole.sh start              start Pi-hole (refuses if port 53 is taken)
#   ./pihole.sh lists [pro|normal] add the HaGeZi blocklist at that level (default: pro)
#   ./pihole.sh privacy [on|off]   on: website names are never stored; off: for troubleshooting
#   ./pihole.sh test               ad domains -> 0.0.0.0, normal sites resolve
# Afterwards:
#   ./pihole.sh health             is it running and blocking?
#   ./pihole.sh update             pull the latest Pi-hole and restart it
set -euo pipefail
cd "$(dirname "$0")"
SELF=$(basename "$0")

HOST_DNS4="1.1.1.1 9.9.9.9"
HOST_DNS6="2606:4700:4700::1111 2620:fe::fe"
AD_DOMAINS="pagead2.googlesyndication.com ad.doubleclick.net"
OK_DOMAINS="example.com wikipedia.org"
HAGEZI=https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock
RESOLV_CONF=${PIHOLE_RESOLV_CONF:-/etc/resolv.conf}
DOCKER="sudo docker"
FAILED=0

say()  { echo "[pihole] $*"; }
ok()   { echo "  OK    $*"; }
warn() { echo "  WARN  $*"; }
fail() { echo "  FAIL  $*"; FAILED=1; }
confirm() { local a; read -r -p "$1 [y/N] " a || true; [[ "$a" =~ ^[Yy] ]]; }

# The device and address that reach the internet: the Pi's LAN link and IP.
route_field() { ip -4 route get 1.1.1.1 2>/dev/null | awk -v f="$1" '{for (i = 1; i < NF; i++) if ($i == f) { print $(i + 1); exit }}'; }
pi_ip()   { route_field src; }
lan_dev() { route_field dev; }
host_dns() { awk '/^nameserver/ {print $2}' "$RESOLV_CONF" 2>/dev/null | paste -sd' ' -; }

# Active Ethernet / Wi-Fi connections, one "UUID:TYPE:DEVICE" per line.
active_conns() { nmcli -g UUID,TYPE,DEVICE connection show --active 2>/dev/null | grep -E ':(802-3-ethernet|802-11-wireless):' || true; }

# Whatever is listening on a port, as `ss` shows it (empty when free).
port_users() { sudo ss -H -tulpn "sport = :$1" 2>/dev/null || true; }

have_docker()    { command -v docker >/dev/null; }
pihole_state()   { $DOCKER inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' pihole 2>/dev/null || true; }
pihole_running() { have_docker && [ "$($DOCKER inspect -f '{{.State.Running}}' pihole 2>/dev/null || true)" = "true" ]; }
gravity_sql()    { $DOCKER exec pihole pihole-FTL sqlite3 /etc/pihole/gravity.db "$1"; }

# First IPv4 answer from a DNS server, or nothing.
lookup() { dig +short +time=2 +tries=1 @"$1" "$2" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1 || true; }

boot_storage() {
  local src; src=$(findmnt -n -o SOURCE / 2>/dev/null || true)
  src=$(readlink -f "$src" 2>/dev/null || echo "$src")
  case "$src" in
    /dev/mmcblk*) echo "microSD card ($src)" ;;
    /dev/nvme*)   echo "NVMe SSD ($src)" ;;
    /dev/sd*)     echo "USB drive ($src)" ;;
    *)            echo "unknown (${src:-?})" ;;
  esac
}

check_port() {  # $1 port, $2 what needs it
  local busy; busy=$(port_users "$1")
  if [ -z "$busy" ]; then ok "Port $1 is free ($2)"; return 0; fi
  fail "Port $1 is in use — $2 can't start until that stops:"
  echo "$busy" | sed 's/^/        /'
  return 1
}

wait_healthy() {
  local i state=""
  say "Waiting for Pi-hole to report healthy..."
  for i in $(seq 1 60); do
    state=$(pihole_state)
    case "$state" in healthy|running) ok "Pi-hole is $state"; return 0 ;; esac
    sleep 3
  done
  fail "Pi-hole isn't healthy (${state:-not running}) — look at: sudo docker logs pihole"
  return 1
}

cmd_check() {
  local dev ip6 storage os
  say "What's on this Pi (nothing is changed)"
  os=$( (. /etc/os-release && echo "$PRETTY_NAME") 2>/dev/null || echo unknown)
  [ -f /etc/rpi-issue ] && os="Raspberry Pi OS, $os"
  dev=$(lan_dev)
  storage=$(boot_storage)
  echo "  Model:         $(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo unknown)"
  echo "  OS:            $os, $(dpkg --print-architecture 2>/dev/null || uname -m)"
  echo "  Boot storage:  $storage"
  echo "  Free space:    $(df -h --output=avail / 2>/dev/null | tail -1 | tr -d ' ') on /"
  echo "  LAN:           ${dev:-?} $(pi_ip), MAC $(cat "/sys/class/net/${dev:-eth0}/address" 2>/dev/null || echo '?')"
  echo "  Pi's own DNS:  $(host_dns)"
  ip6=$(ip -6 -o addr show dev "${dev:-eth0}" scope global 2>/dev/null | awk '{print $4}' | paste -sd' ' -)
  if [ -n "$ip6" ]; then
    echo "  IPv6:          yes ($ip6) — your network hands out IPv6, so do the IPv6 step in README.md"
  else
    echo "  IPv6:          none from the network (the IPv6 step in README.md is a quick check only)"
  fi

  if systemctl is-active --quiet NetworkManager 2>/dev/null; then ok "NetworkManager manages the network"
  else fail "NetworkManager isn't running — 'host-dns' needs it; ask before going further"; fi
  if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    warn "systemd-resolved is running; if it holds port 53 (below), its stub listener must be turned off"
  fi
  if have_docker; then ok "$(docker --version 2>/dev/null)"
  else warn "Docker isn't installed yet — next: ./pihole.sh install-docker"; fi
  if pihole_running; then ok "Pi-hole is already running (it holds ports 53 and 8080)"
  else
    check_port 53 "Pi-hole's DNS" || true
    check_port 8080 "Pi-hole's web page" || true
  fi
  case "$storage" in
    microSD*) say "Booting from microSD: query history is kept 7 days and saved every 5 minutes (docker-compose.yml)." ;;
    NVMe*|USB*) say "Booting from a drive: the 7-day history in docker-compose.yml is conservative; it can be raised." ;;
  esac
}

cmd_install_docker() {
  if have_docker; then ok "Docker is already installed: $(docker --version)"; return 0; fi
  say "This installs Docker with docker.com's official install script."
  say "Docker adds its own firewall rules on this Pi only; the rest of your network is untouched."
  confirm "Install Docker now?" || { say "Nothing changed."; return 1; }
  local tmp; tmp=$(mktemp)
  curl -fsSL https://get.docker.com -o "$tmp"
  sudo sh "$tmp"
  rm -f "$tmp"
  sudo systemctl enable --now docker
  ok "$(docker --version)"
}

cmd_host_dns() {
  local conns uuid type dev
  conns=$(active_conns)
  if [ -z "$conns" ]; then fail "No active Ethernet or Wi-Fi connection found in NetworkManager"; return 1; fi
  say "The Pi will use public DNS ($HOST_DNS4) and ignore the DNS your router hands out,"
  say "so it stays online even if Pi-hole stops. On Wi-Fi it also turns off power saving,"
  say "which otherwise makes the Pi slow to answer. This applies to:"
  while IFS=: read -r -u 3 uuid type dev; do
    echo "  - '$(nmcli -g connection.id connection show "$uuid" 2>/dev/null)' on $dev (DNS now: $(nmcli -g IP4.DNS device show "$dev" 2>/dev/null | paste -sd' ' -))"
  done 3<<<"$conns"
  confirm "Change the Pi's own DNS now? The connection stays up." || { say "Nothing changed."; return 1; }
  while IFS=: read -r -u 3 uuid type dev; do
    sudo nmcli connection modify "$uuid" ipv4.dns "$HOST_DNS4" ipv4.ignore-auto-dns yes
    case "$(nmcli -g ipv6.method connection show "$uuid" 2>/dev/null)" in
      auto|dhcp|manual) sudo nmcli connection modify "$uuid" ipv6.dns "$HOST_DNS6" ipv6.ignore-auto-dns yes ;;
    esac
    sudo nmcli device reapply "$dev" || sudo nmcli connection up "$uuid"
    if [ "$type" = 802-11-wireless ]; then
      # Wi-Fi power saving makes the Pi slow to answer. Saved for the next
      # connect (after the reapply, so Wi-Fi doesn't reconnect) and off now.
      sudo nmcli connection modify "$uuid" 802-11-wireless.powersave 2
      if sudo iw dev "$dev" set power_save off 2>/dev/null; then ok "Wi-Fi power saving is off"
      else warn "Wi-Fi power saving switches off at the next reboot"; fi
    fi
  done 3<<<"$conns"
  sleep 2
  ok "The Pi's DNS is now: $(host_dns)"
  if getent hosts example.com >/dev/null; then ok "The Pi can still look up websites"
  else fail "The Pi can't look up example.com — undo with the commands in README.md"; return 1; fi
}

cmd_start() {
  have_docker || { fail "Docker isn't installed — run ./pihole.sh install-docker first"; return 1; }
  if ! pihole_running; then check_port 53 "Pi-hole's DNS" || return 1; fi
  $DOCKER compose up -d
  wait_healthy || return 1
  say "Settings Pi-hole is using:"
  local key
  for key in dns.upstreams dns.listeningMode database.maxDBdays database.DBinterval misc.privacylevel dns.queryLogging; do
    echo "        $key = $($DOCKER exec pihole pihole-FTL --config "$key" 2>/dev/null | paste -sd' ' - || true)"
  done
  say "Web page: http://$(pi_ip):8080/admin"
  say "First time? Choose the web password:  sudo docker exec -it pihole pihole setpassword"
}

show_lists() {
  echo "  on?  domains  list"
  gravity_sql "SELECT CASE enabled WHEN 1 THEN 'yes' ELSE 'no' END, number, address FROM adlist ORDER BY id;" |
    awk -F'|' '{printf "  %-4s %8s  %s\n", $1, $2, $3}'
  echo "  Blocked domains in total: $(gravity_sql "SELECT value FROM info WHERE property = 'gravity_count';")"
  if [ -n "$(gravity_sql "SELECT 1 FROM adlist WHERE enabled = 1 AND number = 0;")" ]; then
    fail "A list above has 0 domains — it didn't download or wasn't understood"
  fi
}

cmd_lists() {
  local level=$1 file url
  case "$level" in
    pro)    file=pro.txt ;;
    normal) file=multi.txt ;;
    *) echo "Usage: ./pihole.sh lists [pro|normal]" >&2; return 2 ;;
  esac
  pihole_running || { fail "Pi-hole isn't running — run ./pihole.sh start first"; return 1; }
  url="$HAGEZI/$file"
  say "Blocklists: Pi-hole's default list + HaGeZi '$level'. Other HaGeZi levels are switched off, not deleted."
  gravity_sql "UPDATE adlist SET enabled = 0 WHERE address LIKE '%hagezi/dns-blocklists%' AND address <> '$url';
INSERT OR IGNORE INTO adlist (address, enabled, comment) VALUES ('$url', 1, 'HaGeZi $level (pihole.sh)');
UPDATE adlist SET enabled = 1 WHERE address = '$url';"
  say "Downloading the lists (about a minute)..."
  $DOCKER exec pihole pihole -g
  show_lists
}

cmd_test() {
  local ip s d a n bad=""
  command -v dig >/dev/null || { say "Installing dig (a DNS lookup tool)..."; sudo apt-get install -y -qq bind9-dnsutils; }
  ip=$(pi_ip)
  say "Asking Pi-hole directly, on this Pi and at $ip (the address your laptop will use)"
  for s in 127.0.0.1 "$ip"; do
    for d in $AD_DOMAINS; do
      a=$(lookup "$s" "$d")
      if [ "$a" = "0.0.0.0" ]; then ok "$d -> 0.0.0.0 (blocked)  [via $s]"
      else fail "$d -> ${a:-no answer}, expected 0.0.0.0  [via $s]"; fi
    done
    for d in $OK_DOMAINS; do
      a=$(lookup "$s" "$d")
      if [ -n "$a" ] && [ "$a" != "0.0.0.0" ]; then ok "$d -> $a  [via $s]"
      else fail "$d -> ${a:-no answer}, expected a real address  [via $s]"; fi
    done
  done
  say "The Pi's own DNS (must not go through Pi-hole)"
  for n in $(host_dns); do
    case "$n" in 127.*|::1|"$ip") bad="$bad $n" ;; esac
  done
  if [ -n "$bad" ]; then fail "The Pi uses Pi-hole ($bad) for itself — run ./pihole.sh host-dns"
  else ok "The Pi uses $(host_dns)"; fi
  if getent hosts example.com >/dev/null; then ok "The Pi looks up websites without Pi-hole"
  else fail "The Pi can't look up example.com on its own"; fi
  cat <<EOF

From your laptop on the same network, run:
  nslookup pagead2.googlesyndication.com $ip     ->  Address: 0.0.0.0
  nslookup example.com $ip                       ->  real addresses (not 0.0.0.0)
EOF
}

ftl_config() { $DOCKER exec pihole pihole-FTL --config "$@" 2>/dev/null | tr -d '[:space:]' || true; }

# "on" when website names are hidden (privacy level 1+) and no query log file is written.
privacy_state() {
  local level; level=$(ftl_config misc.privacylevel)
  if [ "${level:-0}" -ge 1 ] 2>/dev/null && [ "$(ftl_config dns.queryLogging)" = false ]; then echo on; else echo off; fi
}

cmd_privacy() {
  local mode=$1 level logging
  case "$mode" in
    on)     level=1 logging=false ;;
    off)    level=0 logging=true ;;
    status) say "Privacy is $(privacy_state)"; return 0 ;;
    *) echo "Usage: ./pihole.sh privacy [on|off|status]" >&2; return 2 ;;
  esac
  pihole_running || { fail "Pi-hole isn't running — run ./pihole.sh start first"; return 1; }
  # Not in docker-compose.yml on purpose: settings made there can't be switched here.
  ftl_config misc.privacylevel "$level" >/dev/null
  ftl_config dns.queryLogging "$logging" >/dev/null
  say "Restarting Pi-hole to apply it (DNS pauses a few seconds; devices use 1.1.1.1 meanwhile)."
  if [ "$mode" = on ] && confirm "Also delete the history Pi-hole has stored so far?"; then
    $DOCKER compose down
    sudo rm -f etc-pihole/pihole-FTL.db etc-pihole/pihole-FTL.db-wal etc-pihole/pihole-FTL.db-shm
    $DOCKER compose up -d
    ok "Stored history deleted"
  else
    $DOCKER restart pihole >/dev/null
  fi
  wait_healthy || return 1
  echo "        misc.privacylevel = $(ftl_config misc.privacylevel), dns.queryLogging = $(ftl_config dns.queryLogging)"
  if [ "$(privacy_state)" != "$mode" ]; then fail "Privacy didn't switch $mode"; return 1; fi
  if [ "$mode" = on ]; then ok "Privacy on: website names are hidden and never stored"
  else warn "Privacy off: website names are recorded again. Switch back when done: ./pihole.sh privacy on"; fi
}

cmd_health() {
  pihole_running || { fail "Pi-hole is not running — start it with ./pihole.sh start"; return 1; }
  ok "Container: $($DOCKER inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}, up since {{.State.StartedAt}}, restarts: {{.RestartCount}}' pihole)"
  if command -v dig >/dev/null; then
    a=$(lookup 127.0.0.1 pagead2.googlesyndication.com)
    if [ "$a" = "0.0.0.0" ]; then ok "Blocking ads"; else fail "Not blocking (got ${a:-no answer})"; fi
    a=$(lookup 127.0.0.1 example.com)
    if [ -n "$a" ] && [ "$a" != "0.0.0.0" ]; then ok "Answering normal lookups"; else fail "Not answering normal lookups"; fi
  else
    warn "dig isn't installed; run ./pihole.sh test once to install it"
  fi
  ok "Blocklists updated $(gravity_sql "SELECT datetime(value, 'unixepoch', 'localtime') FROM info WHERE property = 'updated';"), $(gravity_sql "SELECT value FROM info WHERE property = 'gravity_count';") domains"
  if [ "$(privacy_state)" = on ]; then ok "Privacy on: website names are not stored"
  else warn "Privacy is off: website names are being recorded — ./pihole.sh privacy on"; fi
  ok "Pi-hole data $(sudo du -sh etc-pihole 2>/dev/null | cut -f1), free on the Pi $(df -h --output=avail / | tail -1 | tr -d ' ')"
  $DOCKER exec pihole pihole -v 2>/dev/null | sed 's/^/        /' || true
}

cmd_update() {
  have_docker || { fail "Docker isn't installed"; return 1; }
  say "Updating Pi-hole; DNS pauses ~30s while it restarts (devices use 1.1.1.1 meanwhile)."
  $DOCKER compose pull
  $DOCKER compose up -d
  $DOCKER image prune -f >/dev/null
  wait_healthy && cmd_health
}

case "${1:-help}" in
  check)          cmd_check ;;
  install-docker) cmd_install_docker ;;
  host-dns)       cmd_host_dns ;;
  start)          cmd_start ;;
  lists)          cmd_lists "${2:-pro}" ;;
  privacy)        cmd_privacy "${2:-on}" ;;
  test)           cmd_test ;;
  health)         cmd_health ;;
  update)         cmd_update ;;
  *)              sed -n '2,14p' "$SELF" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
[ "$FAILED" = 0 ]
