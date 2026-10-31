#!/usr/bin/env bash
# pi_deploy.sh — set up / update all Jarvis agents on the Raspberry Pi.
#
# Installs them as *user* systemd services (systemctl --user), matching the
# existing `jarvis` voice-loop service — so no sudo is needed.
#
# Run on the Pi from anywhere:
#   bash ~/jarvis-home/deploy/pi_deploy.sh     (wherever you cloned the repo)
#
# Re-runnable any time (e.g. after `git pull`) to pick up code changes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$REPO/agents"
VENV="$AGENTS/venv"
UNIT_DIR="$HOME/.config/systemd/user"

echo "[deploy] repo   = $REPO"
echo "[deploy] agents = $AGENTS"
mkdir -p "$UNIT_DIR"

# 1. shared virtualenv + dependencies
if [ ! -d "$VENV" ]; then
  echo "[deploy] creating venv..."
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -U pip
"$VENV/bin/pip" install -q -r "$REPO/deploy/requirements-pi.txt"
echo "[deploy] venv ready"

# 2. let user services keep running without an active login (survive reboot)
loginctl enable-linger "$(whoami)" 2>/dev/null || true

# 3. agent -> port map
declare -A PORT=(
  [andy_mann]=5001 [drew_parture]=5002 [shawn_ping]=5003
  [will_due]=5004  [miles_ahead]=5005  [jarvis]=5006
  [ray_mote]=5007
)

# 4. generate + install a user systemd unit per agent
for name in "${!PORT[@]}"; do
  dir="$AGENTS/$name"
  [ -d "$dir" ] || { echo "[deploy] SKIP $name (missing $dir)"; continue; }
  [ -f "$dir/.env" ] || echo "[deploy] WARNING: $dir/.env missing — $name will error until you add it"
  cat > "$UNIT_DIR/jarvis-$name.service" <<EOF
[Unit]
Description=Jarvis agent: $name
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$dir
EnvironmentFile=-$dir/.env
Environment=PORT=${PORT[$name]}
# Source the shared secrets file (JARVIS_SECRET, ANTHROPIC_API_KEY, …) exactly
# like the voice loop does, then run. Per-agent vars come from EnvironmentFile.
ExecStart=/bin/bash -c 'set -a; source $HOME/.jarvis_env; set +a; exec $VENV/bin/python -u pi_run.py'
Restart=always
RestartSec=5
# Logs go to the journal, which rotates and caps itself (the old append-to-
# service.log files grew forever). View: journalctl --user -u jarvis-$name -f
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
  echo "[deploy] installed jarvis-$name.service (port ${PORT[$name]})"
done

# 5. reload + (re)start
systemctl --user daemon-reload
for name in "${!PORT[@]}"; do
  [ -d "$AGENTS/$name" ] || continue
  systemctl --user enable --now "jarvis-$name" >/dev/null 2>&1 || true
  systemctl --user restart "jarvis-$name" \
    && echo "[deploy] started jarvis-$name" \
    || echo "[deploy] jarvis-$name FAILED — journalctl --user -u jarvis-$name -n 40"
done

# The voice loop (set up separately, docs/setup.md §8) reads registry.json and
# the router at start, so restart it too or it won't know about new agents.
if [ -f "$UNIT_DIR/jarvis.service" ]; then
  systemctl --user restart jarvis \
    && echo "[deploy] restarted the voice loop (jarvis)" \
    || echo "[deploy] voice loop FAILED to restart — journalctl --user -u jarvis -n 40"
fi

echo ""
echo "[deploy] done. Local health check (agents can take ~10-20s to boot on a Pi):"
for name in andy_mann drew_parture shawn_ping will_due miles_ahead jarvis ray_mote; do
  [ -d "$AGENTS/$name" ] || continue
  p=${PORT[$name]}
  printf "  %-13s :%s -> " "$name" "$p"
  reply=""
  for _ in $(seq 1 15); do
    if reply=$(curl -s -m 2 "http://127.0.0.1:$p/"); then break; fi
    reply=""
    sleep 2
  done
  if [ -n "$reply" ]; then
    echo "$reply"
  else
    echo "(no response after 30s) — journalctl --user -u jarvis-$name -n 40"
  fi
done
