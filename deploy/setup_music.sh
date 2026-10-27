#!/usr/bin/env bash
# setup_music.sh — radio and AirPlay on the Jarvis speaker (docs/music.md).
#
# Installs mpv (the radio) and shairport-sync (AirPlay), and lets Jarvis's
# voice, the radio and AirPlay share the one USB speaker: a raw hw: device
# plays one sound at a time, so this adds an ALSA mixing device ("jarvis")
# in /etc/asound.conf and points everything at it.
#
# Run on the Pi as your normal user (it uses sudo where needed):
#   bash ~/jarvis-home/deploy/setup_music.sh            # AirPlay name "Jarvis"
#   bash ~/jarvis-home/deploy/setup_music.sh "Kitchen"  # or your own
#
# Re-runnable. Previous /etc/asound.conf and /etc/shairport-sync.conf are
# kept as *.before-jarvis the first time.
set -euo pipefail

# As yourself, not root: the settings go in YOUR ~/.jarvis_env, and Jarvis is
# one of your user services. The script asks for sudo itself where needed.
if [ "$(id -u)" -eq 0 ]; then
  echo "[music] Run this without sudo: bash ~/jarvis-home/deploy/setup_music.sh" >&2
  exit 1
fi

AIRPLAY_NAME="${1:-Jarvis}"
ENV_FILE="$HOME/.jarvis_env"
FLAG=/dev/shm/jarvis-airplay

# The speaker, from JARVIS_AUDIO_OUT (default hw:2,0, as in jarvis_core/config.py)
OUT="hw:2,0"
if [ -f "$ENV_FILE" ]; then
  line=$(grep -E '^JARVIS_AUDIO_OUT=' "$ENV_FILE" | tail -1 || true)
  [ -n "$line" ] && OUT="${line#JARVIS_AUDIO_OUT=}"
fi
OUT="${OUT%%#*}"; OUT="${OUT//[[:space:]]/}"
if [[ "$OUT" =~ ^(plug)?hw:(CARD=)?([^,]+)(,(DEV=)?([0-9]+))?$ ]]; then
  CARD="${BASH_REMATCH[3]}"; DEV="${BASH_REMATCH[6]:-0}"
else
  echo "[music] JARVIS_AUDIO_OUT=$OUT is not a hw:CARD,DEV speaker — set it first (aplay -l)." >&2
  exit 1
fi
echo "[music] speaker = hw:$CARD,$DEV, AirPlay name = \"$AIRPLAY_NAME\""

# 1. packages
sudo apt-get update -qq
sudo apt-get install -y -qq mpv shairport-sync alsa-utils

# 2. a mixing device on the speaker, shared by every user (shairport-sync runs as its own)
backup() { [ -f "$1" ] && ! grep -q "setup_music.sh" "$1" && [ ! -f "$1.before-jarvis" ] && sudo cp "$1" "$1.before-jarvis" || true; }
backup /etc/asound.conf
sudo tee /etc/asound.conf >/dev/null <<EOF
# Written by jarvis-home/deploy/setup_music.sh: Jarvis's voice, the radio and
# AirPlay share one speaker. Re-run the script rather than editing this.
pcm.jarvis_mix {
    type dmix
    ipc_key 4242
    ipc_key_add_uid false
    ipc_perm 0666
    ipc_gid audio
    slave {
        pcm "hw:$CARD,$DEV"
        rate 48000
        channels 2
        format S16_LE
        period_size 1024
        buffer_size 8192
    }
}

# Jarvis's voice and the radio (JARVIS_PLAYBACK=jarvis)
pcm.jarvis {
    type plug
    slave.pcm "jarvis_mix"
}

# AirPlay, with its own volume control that Jarvis turns down while it listens
pcm.jarvis_airplay_vol {
    type softvol
    slave.pcm "jarvis_mix"
    control {
        name "AirPlay"
        card $CARD
    }
    min_dB -51.0
    max_dB 0.0
}
pcm.jarvis_airplay {
    type plug
    slave.pcm "jarvis_airplay_vol"
}
EOF
echo "[music] wrote /etc/asound.conf"

# 3. point the voice loop at the mixing device
if [ -f "$ENV_FILE" ] && grep -qE '^JARVIS_PLAYBACK=' "$ENV_FILE"; then
  sed -i 's/^JARVIS_PLAYBACK=.*/JARVIS_PLAYBACK=jarvis/' "$ENV_FILE"
else
  printf '\n# Speaker shared with the radio and AirPlay (deploy/setup_music.sh)\nJARVIS_PLAYBACK=jarvis\n' >> "$ENV_FILE"
fi
echo "[music] JARVIS_PLAYBACK=jarvis in $ENV_FILE"

# 4. AirPlay: play through the mixer, and tell Jarvis while a phone is playing
backup /etc/shairport-sync.conf
sudo tee /etc/shairport-sync.conf >/dev/null <<EOF
// Written by jarvis-home/deploy/setup_music.sh. Re-run the script rather than editing this.
general = {
  name = "$AIRPLAY_NAME";
  output_backend = "alsa";
};
alsa = {
  output_device = "jarvis_airplay";
};
sessioncontrol = {
  run_this_before_play_begins = "/usr/bin/touch $FLAG";
  run_this_after_play_ends = "/bin/rm -f $FLAG";
  wait_for_completion = "no";
  allow_session_interruption = "yes";
};
EOF
# A crash mid-song would leave the flag behind (Jarvis would think AirPlay is on)
sudo mkdir -p /etc/systemd/system/shairport-sync.service.d
sudo tee /etc/systemd/system/shairport-sync.service.d/jarvis.conf >/dev/null <<EOF
[Service]
ExecStartPre=-/bin/rm -f $FLAG
ExecStopPost=-/bin/rm -f $FLAG
EOF
sudo systemctl daemon-reload
# The radio doesn't need AirPlay, so a failure here is reported, not fatal
if sudo systemctl enable shairport-sync >/dev/null 2>&1 && sudo systemctl restart shairport-sync; then
  echo "[music] shairport-sync running as \"$AIRPLAY_NAME\""
else
  echo "[music] WARNING: AirPlay (shairport-sync) didn't start; the radio still works."
  echo "        See: sudo journalctl -u shairport-sync -n 30"
fi

# 5. restart Jarvis so it plays through the mixer
if systemctl --user list-unit-files jarvis.service >/dev/null 2>&1; then
  systemctl --user restart jarvis && echo "[music] restarted the jarvis voice service" || true
fi

echo ""
echo "[music] done. Try:"
echo "  speaker-test -D jarvis -c 2 -t sine -l 1     # a tone through the mixer"
echo "  \"Hey Jarvis, play some jazz\"                  # the radio"
echo "  iPhone → Control Center → AirPlay → $AIRPLAY_NAME"
