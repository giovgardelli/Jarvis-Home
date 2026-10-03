import os
from jarvis_core.config import TIMEZONE
import io
import time
import wave
import random
import threading
import traceback
import subprocess
import requests
import pyaudio
import numpy as np
import timer_manager
import led_controller as led
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from openwakeword.model import Model
import pytz
from jarvis_core.router import route as router_route
from jarvis_core.config import VOICE_ID_ENABLED
from voice_id import identify as voice_id_identify
from voice_id import warmup as voice_id_warmup
if VOICE_ID_ENABLED:
    voice_id_warmup()  # loads the voice model (skipped entirely while voice ID is off)
from concurrent.futures import ThreadPoolExecutor
from jarvis_core.config import AUDIO_OUT_DEVICE, MIC_NAME, home_path, wakeword_model_path
from jarvis_core import household
PEOPLE_KEYTERMS = [f"{name}:5" for name in household.keyterms()]
_executor = ThreadPoolExecutor(max_workers=2)
 
# ── Config ────────────────────────────────────────────────────────────────────
JARVIS_BRIEF_URL = "http://127.0.0.1:5006/get-brief-text"
ELEVENLABS_API_KEY  = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
SPEAKER_ID       = household.DEFAULT_SPEAKER
JARVIS_SECRET    = os.environ["JARVIS_SECRET"]
 
BRIEF_AUDIO_PATH = home_path("brief_gio.mp3")
BRIEF_TEXT_PATH  = home_path("brief_gio_text.txt")

WAKEWORD_SAMPLES_DIR = home_path("wakeword_samples")
WAKEWORD_LOG_THRESHOLD = 0.3
WAKEWORD_BUFFER_SECONDS = 2.5
WAKEWORD_MAX_SAMPLES = 500   # oldest samples are deleted beyond this
VOICE_RESPONSES_DIR = home_path("voice_responses")
ERROR_AUDIO_PATH = os.path.join(VOICE_RESPONSES_DIR, "error.mp3")
 
SAMPLE_RATE  = 16000
CHANNELS     = 2
CHUNK        = 1024
RECORD_SECONDS = 4
FORMAT       = pyaudio.paInt16
PT           = pytz.timezone(TIMEZONE)
 
 
# Conversation history — last 6 exchanges (12 messages)
conversation_history = []
last_full_response = None
_was_quiet = False  # LED quiet hours (10pm-7am): lights off, still listening
MAX_HISTORY = 12
 
_audio_rolling_buffer = []
_audio_buffer_lock = __import__("threading").Lock()
BUFFER_MAX_CHUNKS = None

_whisper_model = None
 
 
# ── Helpers ───────────────────────────────────────────────────────────────────
 
def today_str() -> str:
    return datetime.now(PT).strftime("%Y-%m-%d")


def identify_speaker_voice(audio_bytes: bytes) -> dict:
    """Identify the speaker. Any failure returns status "error", which is
    treated as "please repeat" — never as a default speaker (fail closed).
    With voice ID off the speaker is unknown (status "disabled", speaker None):
    the router asks "Gio or Kenz?" when it matters (the to-do list)."""
    if not VOICE_ID_ENABLED:
        return {"status": "disabled", "speaker": None, "score": 0.0}
    try:
        return voice_id_identify(audio_bytes, SAMPLE_RATE)
    except Exception as e:
        print(f"[VoiceID] Error: {e}", flush=True)
        return {"status": "error", "speaker": None, "score": 0.0}
 
 
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
from deepgram import DeepgramClient
_deepgram_client = DeepgramClient(DEEPGRAM_API_KEY)
from elevenlabs import ElevenLabs
_elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
 
 
def find_audio_device(name_fragment: str) -> int:
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if name_fragment.lower() in d["name"].lower() and d["maxInputChannels"] > 0:
            p.terminate()
            print(f"Found audio device: {d['name']} at index {i}", flush=True)
            return i
    p.terminate()
    raise RuntimeError(f"Audio device '{name_fragment}' not found")


TTS_KWARGS = dict(model_id="eleven_flash_v2_5", output_format="mp3_44100_128")
APLAY_CMD = ["aplay", "-r", "48000", "-f", "S16_LE", "-c", "2", "-D", AUDIO_OUT_DEVICE]
ACK_FILES = [home_path("acks", f) for f in
             ("on_it.mp3", "one_moment.mp3", "let_me_check.mp3", "looking_into_that.mp3", "right_away.mp3")]


def synthesize(text: str) -> bytes:
    """Full MP3 for text (used where audio is stored or replayed: brief, timers)."""
    audio = _elevenlabs_client.text_to_speech.convert(
        voice_id=ELEVENLABS_VOICE_ID, text=text, **TTS_KWARGS
    )
    return b"".join(audio)


# One sound at a time: the speaker device (hw:2,0) can't be shared, so a timer
# going off mid-reply now waits for the reply to finish instead of failing.
_speaker_lock = threading.Lock()


def _to_speaker(input_args, feed=None):
    """Decode audio with ffmpeg (resampled to the speaker's 48kHz stereo) and
    pipe it straight into aplay. `feed` is an iterable of MP3 chunks written
    to ffmpeg's stdin as they arrive; without it ffmpeg reads input_args."""
    with _speaker_lock:
        _to_speaker_locked(input_args, feed)


def _to_speaker_locked(input_args, feed):
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "quiet", *input_args,
         "-ar", "48000", "-ac", "2", "-f", "s16le", "pipe:1"],
        stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    aplay = subprocess.Popen(APLAY_CMD, stdin=ffmpeg.stdout)
    ffmpeg.stdout.close()
    try:
        if feed is not None:
            try:
                for chunk in feed:
                    if chunk:
                        ffmpeg.stdin.write(chunk)
            except BrokenPipeError:
                pass
            finally:
                ffmpeg.stdin.close()
    finally:
        aplay.wait()
        ffmpeg.wait()


def play_audio_bytes(audio_bytes: bytes):
    """Play MP3 bytes. Single decode, no temp file (it used to decode twice)."""
    _to_speaker(["-f", "mp3", "-i", "pipe:0"], feed=[audio_bytes])


def play_file(path: str):
    _to_speaker(["-i", path])


def speak(text: str):
    """Synthesize and play, streaming: playback starts on the first audio
    chunk from ElevenLabs instead of after the whole clip is generated."""
    tts = _elevenlabs_client.text_to_speech
    # .stream() is ElevenLabs' progressive endpoint; older SDKs only have .convert()
    synth = tts.stream if hasattr(tts, "stream") else tts.convert
    _to_speaker(["-f", "mp3", "-i", "pipe:0"],
                feed=synth(voice_id=ELEVENLABS_VOICE_ID, text=text, **TTS_KWARGS))


def play_ack():
    ack_file = random.choice(ACK_FILES)
    if os.path.exists(ack_file):
        play_file(ack_file)

def _save_wakeword_sample(score: float, frames: list):
    """Save one near-miss/wake sample (mono WAV) for wake word tuning.
    Called once per event with the audio at the event's peak score (it used
    to save on every chunk above the threshold — dozens of files per
    "Hey Jarvis"). Keeps at most WAKEWORD_MAX_SAMPLES files."""
    if not frames:
        return
    os.makedirs(WAKEWORD_SAMPLES_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    score_str = f"{score:.2f}".replace(".", "")
    filename = f"wakeword_{timestamp}_score{score_str}.wav"
    try:
        with wave.open(os.path.join(WAKEWORD_SAMPLES_DIR, filename), "wb") as wf:
            wf.setnchannels(1)  # mono — buffer is already downmixed
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(np.concatenate(frames).astype(np.int16).tobytes())
        print(f"[WW Sample] Saved {filename} (score={score:.2f})", flush=True)
        samples = sorted(
            (os.path.join(WAKEWORD_SAMPLES_DIR, f) for f in os.listdir(WAKEWORD_SAMPLES_DIR)
             if f.startswith("wakeword_")),
            key=os.path.getmtime,
        )
        for old in samples[:-WAKEWORD_MAX_SAMPLES]:
            os.remove(old)
    except Exception as e:
        print(f"[WW Sample] Save error: {e}", flush=True)


def record_command(p, device, max_seconds=6) -> bytes:
    """Record with VAD — stops early when silence detected after speech.
    Returns raw interleaved PCM; prepare_audio() turns it into WAVs."""
    print("Recording command...", flush=True)

    SILENCE_THRESHOLD = 300
    SILENCE_DURATION  = 0.8   # seconds of silence that end the command (was 1.0)
    MIN_SPEECH_DURATION = 0.5

    stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                    input=True, input_device_index=device,
                    frames_per_buffer=CHUNK)

    frames = []
    silence_chunks = 0
    speech_chunks = 0
    silence_limit = int(SAMPLE_RATE / CHUNK * SILENCE_DURATION)
    min_speech_chunks = int(SAMPLE_RATE / CHUNK * MIN_SPEECH_DURATION)
    max_chunks = int(SAMPLE_RATE / CHUNK * max_seconds)
    heard_speech = False

    for _ in range(max_chunks):
        data = stream.read(CHUNK)
        frames.append(data)
        audio_chunk = np.frombuffer(data, dtype=np.int16)
        if CHANNELS == 2:
            audio_chunk = audio_chunk[::2]
        rms = np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))

        if rms > SILENCE_THRESHOLD:
            speech_chunks += 1
            silence_chunks = 0
            if speech_chunks >= min_speech_chunks:
                heard_speech = True
        else:
            if heard_speech:
                silence_chunks += 1
                if silence_chunks >= silence_limit:
                    print(f"[VAD] Speech ended after {len(frames)*CHUNK/SAMPLE_RATE:.1f}s", flush=True)
                    break

    stream.stop_stream()
    stream.close()
    return b"".join(frames)


def _wav(samples: np.ndarray, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def prepare_audio(raw: bytes):
    """Raw mic PCM → (WAV for Deepgram, WAV for voice ID), both with the 3x
    software gain the far-field mic needs.

    Voice ID gets the boosted stereo WAV exactly as before — the saved voice
    profiles and thresholds are tuned to it, so don't change it without
    re-enrolling. Deepgram gets the same boosted audio averaged to mono
    (half the upload, same content)."""
    GAIN = 3
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.int32)
    boosted = np.clip(pcm * GAIN, -32768, 32767)
    stt = boosted.reshape(-1, 2).mean(axis=1) if CHANNELS == 2 else boosted
    return _wav(stt, 1), _wav(boosted, CHANNELS)
 

def transcribe(audio_bytes: bytes) -> str:
    deepgram = _deepgram_client
    payload = {"buffer": audio_bytes, "mimetype": "audio/wav"}
    options = {
        "model": "nova-3",
        "language": "en",
        "smart_format": True,
        "keyterm": [
            # Commands
            "add:5", "remove:5", "delete:5", "mark:5", "complete:5",
            "create:5", "cancel:5", "update:5", "check:5", "refresh:5",
            # Jarvis + agents
            "Jarvis:5", "Andy:4", "Drew:4", "Shawn:4", "Will:4",
            # Will Due — tasks
            "task:4", "tasks:4", "to-do:4", "todo:4", "priority:4",
            "priorities:4", "overdue:4", "pending:4",
            # Shawn Ping — shopping
            "shopping:4", "groceries:4", "milk:3", "eggs:3", "bread:3",
            "butter:3", "juice:3", "fruit:3", "vegetables:3", "chicken:3",
            "pasta:3", "coffee:3", "wine:3", "cheese:3", "yogurt:3",
            # Andy Mann — home maintenance
            "maintenance:4", "HVAC:5", "filter:4", "plumber:4",
            "contractor:4", "warranty:4", "appliance:4", "roof:4",
            "gutters:4", "furnace:4", "heater:4", "electrical:4",
            "inspection:4", "seasonal:3", "repair:3",
            # Drew Parture — flights
            "flight:5", "flights:5", "Southwest:5", "departure:4",
            "arrival:4", "boarding:4", "airport:4", "LAX:5", "SFO:5",
            "JFK:4", "confirmation:4", "airline:4", "travel:4",
            # Miles Ahead — price tracking
            "price:4", "fare:4", "savings:4", "miles:4",
            # Messaging / reminders
            "text:4", "message:4", "remind:4",
            # Time and appointments
            "today:4", "tomorrow:4", "morning:4", "afternoon:4",
            "evening:4", "appointment:4", "barber:5", "dentist:4", "meeting:4",
            "brief:5", "summary:4",
            # People in household.json (names + aliases)
            *PEOPLE_KEYTERMS,
        ]
    }
    response = deepgram.listen.rest.v("1").transcribe_file(payload, options)
    try:
        # Try object access first (SDK v3)
        transcript = response.results.channels[0].alternatives[0].transcript
    except (AttributeError, TypeError):
        # Fall back to dict access
        transcript = response["results"]["channels"][0]["alternatives"][0]["transcript"]
    print(f"[Deepgram] Raw transcript: {transcript}", flush=True)
    return transcript.strip() 
 
# ── Morning brief ─────────────────────────────────────────────────────────────
 
def brief_ready_today() -> bool:
    """True if today's brief audio has already been fetched and generated."""
    if not os.path.exists(BRIEF_AUDIO_PATH):
        return False
    modified = datetime.fromtimestamp(os.path.getmtime(BRIEF_AUDIO_PATH), PT).date()
    return modified == datetime.now(PT).date()


def fetch_and_store_brief():
    """Fetch Gio's brief from the central Jarvis service and generate audio.

    Scheduled several times between 7:05 and 7:45 because the central service
    only starts generating the brief at 7:00 and it takes a while. Each run is
    a no-op once today's brief is stored, and a brief dated any other day is
    rejected so yesterday's brief is never replayed as today's.
    """
    if brief_ready_today():
        return
    print("Fetching morning brief...", flush=True)
    try:
        resp = requests.post(
            JARVIS_BRIEF_URL,
            json={"speaker": "gio"},
            headers={"X-Jarvis-Secret": JARVIS_SECRET},
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        brief_text = data.get("brief_text", "")
        brief_audio_text = data.get("brief_audio_text", brief_text)
 
        if not brief_text:
            print("No brief text returned yet — will retry.", flush=True)
            return
        if data.get("date") != today_str():
            print(f"Brief is dated {data.get('date')}, not today — will retry.", flush=True)
            return
 
        # Save text for fallback
        with open(BRIEF_TEXT_PATH, "w") as f:
            f.write(brief_text)
 
        # Generate and save audio
        print("Generating brief audio...", flush=True)
        audio_bytes = synthesize(brief_audio_text)
        with open(BRIEF_AUDIO_PATH, "wb") as f:
            f.write(audio_bytes)
        print("Morning brief ready.", flush=True)
 
    except Exception as e:
        print(f"Brief fetch error: {e}", flush=True)
 
 
BRIEF_TRIGGERS = [
    "morning brief", "daily brief", "today's brief", "today's summary",
    "morning summary", "daily summary", "good morning", "goodmorning",
    "what's my brief", "whats my brief", "give me my brief",
    "morning update", "daily update", "today's update"
]

def is_brief_request(text: str) -> bool:
    t = text.lower()
    return any(trigger in t for trigger in BRIEF_TRIGGERS)

def play_brief_if_available() -> bool:
    """Play cached audio brief. Returns True if played."""
    if not os.path.exists(BRIEF_AUDIO_PATH):
        return False
    # Check if brief is from today
    if os.path.exists(BRIEF_TEXT_PATH):
        modified = datetime.fromtimestamp(
            os.path.getmtime(BRIEF_AUDIO_PATH), PT
        ).date()
        if modified != datetime.now(PT).date():
            return False
    try:
        play_file(BRIEF_AUDIO_PATH)
        return True
    except Exception as e:
        print(f"Brief playback error: {e}", flush=True)
        return False 


# ── Main loop ─────────────────────────────────────────────────────────────────

def _open_stream(p, device):
    return p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                  input=True, input_device_index=device,
                  frames_per_buffer=CHUNK)


def _to_mono(data: bytes) -> np.ndarray:
    audio = np.frombuffer(data, dtype=np.int16)
    return audio[::2] if CHANNELS == 2 else audio


def _resume_listening(p, device, oww_model):
    """Reopen the mic and get the wake word model ready for the next command.

    reset() clears the model's internal buffers (which still hold the last
    "Hey Jarvis"), so a short warm-up replaces the old 2-second flush during
    which Jarvis was deaf. Falls back to the 2s flush on openwakeword
    versions without reset()."""
    stream = _open_stream(p, device)
    if hasattr(oww_model, "reset"):
        oww_model.reset()
        flush_seconds = 0.3
    else:
        flush_seconds = 2
    for _ in range(int(SAMPLE_RATE / CHUNK * flush_seconds)):
        oww_model.predict(_to_mono(stream.read(CHUNK, exception_on_overflow=False)))
    led.listening()
    print("Listening... say 'Hey Jarvis'", flush=True)
    return stream


def _remember(transcript, response_text):
    global conversation_history
    conversation_history.append({"role": "user", "content": transcript})
    conversation_history.append({"role": "assistant", "content": response_text})
    conversation_history = conversation_history[-MAX_HISTORY:]


def _handle_command(p, audio_device, t0):
    """Record → transcribe → route → speak, for one wake-word activation."""
    global last_full_response

    stt_wav, id_wav = prepare_audio(record_command(p, audio_device))
    print(f"[TIMER] Record: {time.time()-t0:.2f}s", flush=True)

    t1 = time.time()
    future_transcript = _executor.submit(transcribe, stt_wav)
    future_voice = _executor.submit(identify_speaker_voice, id_wav)
    transcript = future_transcript.result()
    voice_result = future_voice.result()
    print(f"[TIMER] Transcribe+VoiceID parallel: {time.time()-t1:.2f}s", flush=True)
    print(f"[Deepgram] Transcript: {transcript}", flush=True)
    print(f"[VoiceID] {voice_result}", flush=True)

    if not transcript:
        return

    # Who's speaking. Unrecognized or unknown voices aren't turned away here:
    # the router decides per request what they may do (sensitivity tiers —
    # timers and web questions are public, personal lists are not).
    status = voice_result["status"]
    if status == "confirmed":
        identified_speaker = voice_result.get("speaker") or SPEAKER_ID
        led.identified(identified_speaker)
    elif status == "no_profiles":          # voice ID on but nobody enrolled yet
        identified_speaker = SPEAKER_ID
        led.identified(identified_speaker)
    else:                                  # disabled / uncertain / unauthorized / error
        identified_speaker = None          # the router resolves who it's for
        if status == "disabled":
            led.identified(SPEAKER_ID)
        else:
            led.public_query()

    # The morning brief is personal
    if is_brief_request(transcript):
        if status in ("confirmed", "no_profiles", "disabled"):
            if play_brief_if_available():
                return
        else:
            _play_gate("repeat" if status in ("uncertain", "error") else "unauthorized")
            return

    # Start routing right away and play the ack while it runs (the ack used
    # to finish before routing even began)
    t2 = time.time()
    future_result = _executor.submit(
        router_route,
        text=transcript,
        speaker=identified_speaker,
        history=list(conversation_history),
        last_full=last_full_response,
        voice_mode=True,
        voice_status=status,
    )
    play_ack()
    result = future_result.result()
    print(f"[TIMER] Router (overlapping ack): {time.time()-t2:.2f}s", flush=True)

    # Blocked by its sensitivity tier: play the offline clip, remember nothing
    if result.get("gate"):
        _play_gate(result["gate"], result.get("response_text", ""))
        return

    response_text = result.get("response_text", "")
    full_response = result.get("full_response", response_text)
    print(f"[Jarvis response]: {response_text[:150]}", flush=True)
    force_text = result.get("force_text", False)

    if response_text:
        _remember(transcript, response_text)
    last_full_response = full_response

    if response_text:
        if force_text:
            # Send as Telegram text via the central Jarvis service
            requests.post(
                "http://127.0.0.1:5006/send-text",
                json={"text": response_text, "speaker": identified_speaker or SPEAKER_ID},
                headers={"X-Jarvis-Secret": JARVIS_SECRET},
                timeout=10
            )
        else:
            t3 = time.time()
            speak(response_text)
            print(f"[TIMER] Speak (streamed TTS + playback): {time.time()-t3:.2f}s", flush=True)

    print(f"[TIMER] Total: {time.time()-t0:.2f}s", flush=True)

    # Follow-up window if response ends with a question
    if response_text.rstrip().endswith("?"):
        print("[Follow-up] Response ended with question, opening 4s window...", flush=True)
        try:
            stt_wav, id_wav = prepare_audio(record_command(p, audio_device, max_seconds=4))
            future_voice = _executor.submit(identify_speaker_voice, id_wav)
            followup_transcript = transcribe(stt_wav)
            followup_voice = future_voice.result()
            print(f"[Follow-up] Transcript: {followup_transcript} | VoiceID: {followup_voice}", flush=True)
            # Same person must answer the follow-up. Short replies ("yes")
            # score lower, so "uncertain" for the same speaker is accepted.
            same_speaker = (
                followup_voice["status"] in ("no_profiles", "disabled")
                or (followup_voice["status"] in ("confirmed", "uncertain")
                    and followup_voice.get("speaker") == identified_speaker)
            )
            if not same_speaker:
                print("[Follow-up] Different or unrecognized voice — ignoring.", flush=True)
            elif followup_transcript and len(followup_transcript.strip()) > 2:
                followup_result = router_route(
                    text=followup_transcript,
                    speaker=identified_speaker,
                    history=conversation_history,
                    last_full=last_full_response,
                    voice_mode=True,
                    voice_status=status,
                )
                if followup_result.get("gate"):
                    _play_gate(followup_result["gate"], followup_result.get("response_text", ""))
                    return
                followup_response = followup_result.get("response_text", "")
                if followup_response:
                    _remember(followup_transcript, followup_response)
                    speak(followup_response)
        except Exception as e:
            print(f"[Follow-up] Error: {e}", flush=True)


def _play_gate(gate, text=""):
    """A request blocked by voice ID: "unauthorized" (not a household voice) or
    "repeat" (couldn't tell who it was). Plays the pre-recorded clip
    (generate_voice_responses.py), or speaks the text if it's missing."""
    if gate == "unauthorized":
        led.unauthorized()
        clip, fallback = "unauthorized.mp3", "I'm only able to help the people who live here with that."
    else:
        led.uncertain()
        clip, fallback = "repeat.mp3", "I didn't quite catch who's speaking. Could you try again?"
    path = os.path.join(VOICE_RESPONSES_DIR, clip)
    try:
        if os.path.exists(path):
            play_file(path)
        else:
            speak(text or fallback)
    except Exception as e:
        print(f"[Gate] Could not play {clip}: {e}", flush=True)


def _apologize():
    """Tell the user something failed. Prefers a pre-recorded clip, since the
    failure may be ElevenLabs itself."""
    try:
        if os.path.exists(ERROR_AUDIO_PATH):
            play_file(ERROR_AUDIO_PATH)
        else:
            speak("Sorry, something went wrong. Please try again.")
    except Exception as e:
        print(f"[Error] Could not play apology: {e}", flush=True)


def _reopen_audio(p, oww_model, old_stream=None):
    """Recover the mic after an audio error (e.g. USB hiccup): close the old
    stream, find the device again (its index can change) and reopen. Retries
    until it works rather than letting the service die."""
    if old_stream is not None:
        try:
            old_stream.close()
        except Exception:
            pass
    while True:
        try:
            device = find_audio_device(MIC_NAME)
            stream = _open_stream(p, device)
            if hasattr(oww_model, "reset"):
                oww_model.reset()
            print("[Audio] Microphone reopened.", flush=True)
            return stream, device
        except Exception as e:
            print(f"[Audio] Reopen failed ({e}) — retrying in 5s", flush=True)
            time.sleep(5)


def main():
    global _was_quiet

    print("Finding audio device...", flush=True)
    try:
        audio_device = find_audio_device(MIC_NAME)
    except RuntimeError as e:
        print(f"Audio device error: {e}", flush=True)
        return
    timer_manager.init(
        play_fn=play_audio_bytes,
        synthesize_fn=synthesize
    )

    print("Loading wake word model...", flush=True)
    oww_model = Model(wakeword_model_paths=[wakeword_model_path()])

    # Schedule jobs
    scheduler = BackgroundScheduler(timezone=PT)
    scheduler.add_job(fetch_and_store_brief, "cron", hour=7, minute="5,15,25,35,45")
    scheduler.start()

    p = pyaudio.PyAudio()
    stream = _open_stream(p, audio_device)
    global BUFFER_MAX_CHUNKS
    BUFFER_MAX_CHUNKS = int(SAMPLE_RATE / CHUNK * WAKEWORD_BUFFER_SECONDS)

    print("Jarvis is listening... say 'Hey Jarvis'", flush=True)
    led.listening()

    ww_peak = None  # (score, frames) at the highest score of the current wake event

    try:
        while True:
            try:
                audio_mono = _to_mono(stream.read(CHUNK, exception_on_overflow=False))
            except OSError as e:
                print(f"[Audio] Read error: {e}", flush=True)
                stream, audio_device = _reopen_audio(p, oww_model, stream)
                continue

            # LED quiet hours: lights go dark at 10pm and come back at 7am.
            # Jarvis keeps listening and answering around the clock.
            quiet = led.is_quiet_hours()
            if quiet != _was_quiet:
                _was_quiet = quiet
                if quiet:
                    led.off()
                else:
                    led.listening()

            with _audio_buffer_lock:
                _audio_rolling_buffer.append(audio_mono.copy())
                if BUFFER_MAX_CHUNKS and len(_audio_rolling_buffer) > BUFFER_MAX_CHUNKS:
                    _audio_rolling_buffer.pop(0)

            prediction = oww_model.predict(audio_mono)
            score = list(prediction.values())[0]

            # Keep one sample per wake event: the audio at its peak score,
            # saved once the score drops back (or the wake word fires)
            if score > WAKEWORD_LOG_THRESHOLD:
                print(f"[WW] {score:.2f}", flush=True)
                if ww_peak is None or score > ww_peak[0]:
                    with _audio_buffer_lock:
                        ww_peak = (score, list(_audio_rolling_buffer))
            elif ww_peak is not None:
                _save_wakeword_sample(*ww_peak)
                ww_peak = None

            if score > 0.6:
                print(f"Wake word detected ({score:.2f})", flush=True)
                if ww_peak is not None:
                    _save_wakeword_sample(*ww_peak)
                    ww_peak = None
                led.wake_word()
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                stream.close()

                # One failed request (network blip, API error) must not take
                # Jarvis down: apologize and go back to listening
                try:
                    _handle_command(p, audio_device, time.time())
                except Exception as e:
                    print(f"[Error] Command failed: {e}", flush=True)
                    print(traceback.format_exc(), flush=True)
                    _apologize()
                try:
                    stream = _resume_listening(p, audio_device, oww_model)
                except Exception as e:
                    print(f"[Audio] Resume failed: {e}", flush=True)
                    stream, audio_device = _reopen_audio(p, oww_model)
                    led.listening()
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
        led.off()
    finally:
        scheduler.shutdown()
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()


if __name__ == "__main__":
    main()
