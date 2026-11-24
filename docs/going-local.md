# Going local: built-in experiments

Two parts of Jarvis can run on the Pi instead of the cloud. Both start in
**shadow mode**: Jarvis keeps working exactly as before while the local
version runs alongside it and logs how it would have done. You switch one on
only when the numbers show it's **at least as fast and as accurate**.

| | Track A — routing classifier | Track B — local speech-to-text |
|---|---|---|
| Gain | **Speed**: skips the cloud routing call (~0.5–1 s) for requests it's sure about | **Privacy**: your voice recordings never leave the Pi |
| Cost of being wrong | Falls back to Claude when unsure | Falls back to Deepgram on failure |
| Needs | Nothing — pure Python, trains on your own `routing_log.csv` | [Moonshine](https://github.com/moonshine-ai/moonshine) installed on the Pi (best on a Pi 5) |
| Setting | `ROUTER_CLASSIFIER` = `shadow` (default) / `on` / `off` | `LOCAL_STT` = `off` (default) / `shadow` / `on` |
| Logs | `~/router_shadow.csv` | `~/stt_shadow.csv` |

## Track A — the routing classifier

A small model that learns where *your household's* requests go, from every
decision Jarvis has logged. It runs in milliseconds and retrains itself as the
log grows. It only acts when it's confident, never for small talk (Claude has
to write that reply), and never for "I finished X" requests that need
today's-brief hints.

Nothing to do — it's already in shadow mode. After a week or two of normal
use, run the report (below). Fine-tuning: `ROUTER_CLASSIFIER_MIN_CONFIDENCE`
(default 0.9) and `ROUTER_CLASSIFIER_MIN_SIMILARITY` (default 0.5).

## Track B — local speech-to-text with Moonshine

[Moonshine](https://github.com/moonshine-ai/moonshine) is a speech model built
for small devices: it only processes the audio you actually spoke (Whisper
always processes 30 seconds), which makes short voice commands fast.

```bash
~/jarvis-env/bin/pip install useful-moonshine-onnx
# if pip can't find it, install from the project's repo:
# ~/jarvis-env/bin/pip install "useful-moonshine-onnx@git+https://github.com/moonshine-ai/moonshine.git#subdirectory=moonshine-onnx"

echo "LOCAL_STT=shadow" >> ~/.jarvis_env
systemctl --user restart jarvis
journalctl --user -u jarvis -n 30 | grep LocalSTT     # expect "Moonshine ready"
```

If the log says "Not available", the reason follows (missing package, or a
different API version) — Jarvis carries on with Deepgram regardless.
`LOCAL_STT_MODEL=moonshine/tiny` is faster but less accurate than the default
`moonshine/base`.

Worth knowing: Deepgram can be told to listen for your household's names and
words (`keyterm`s); Moonshine can't. The aliases in `household.json` catch the
common mishearings, and the report shows whether routing still lands in the
same place.

## Reading the report

```bash
cd ~/jarvis-home && python3 -m jarvis_core.shadow_report
```

- **Track A** shows, for each confidence level, how many requests would skip
  the cloud call and how often the classifier agreed with Claude. It says
  **READY** (and which setting to use) once some level reaches **≥ 98%
  agreement over at least 30 requests**.
- **Track B** shows both speeds, how often transcripts match, and how often
  they'd send the request to the same place. **READY** means **no slower than
  Deepgram and ≥ 97% the same decision over at least 30 commands**.

Then set `ROUTER_CLASSIFIER=on` and/or `LOCAL_STT=on` in `~/.jarvis_env` and
restart the voice loop (`systemctl --user restart jarvis`; for Telegram
routing also `jarvis-jarvis`). Switching back is the same setting.
