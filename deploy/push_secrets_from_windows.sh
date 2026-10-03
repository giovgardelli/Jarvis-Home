#!/usr/bin/env bash
# push_secrets_from_windows.sh — upload each agent's .env file to the Pi.
#
# Secrets never go through GitHub: keep agents/<name>/.env on your computer
# (gitignored) and copy them to the Pi over ssh in one connection.
#
# Run from a bash shell (e.g. Git Bash on Windows) in your local clone:
#   bash deploy/push_secrets_from_windows.sh <user>@<pi-address> <repo path on the Pi>
# e.g.
#   bash deploy/push_secrets_from_windows.sh pi@raspberrypi.local /home/pi/jarvis-home
set -e
if [ $# -lt 2 ]; then
  echo "usage: $0 <user>@<pi-address> <repo path on the Pi>" >&2
  exit 1
fi
PI="$1"
REMOTE_REPO="$2"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

cd "$HERE/agents"
echo "Uploading .env files for: $(ls -d */ | tr -d '/' | grep -v '^venv$' | tr '\n' ' ')"
tar -cf - */.env | ssh "$PI" "mkdir -p '$REMOTE_REPO/agents' && tar -C '$REMOTE_REPO/agents' -xf - && echo '--- now on the Pi:' && ls -l $REMOTE_REPO/agents/*/.env"
echo "done."
