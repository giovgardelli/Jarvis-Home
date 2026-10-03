"""
voice_id.py — Voice identification using Resemblyzer
"""
import io
import os
import wave
import numpy as np

from jarvis_core.config import home_path

PROFILES_DIR = home_path("voice_profiles")
# Tunable without code changes (set in ~/.jarvis_env); `python3 enroll.py
# --test` prints the scores to tune against.
SIMILARITY_CONFIRMED = float(os.environ.get("VOICE_ID_CONFIRMED", "0.78"))  # above = identified
SIMILARITY_UNCERTAIN = float(os.environ.get("VOICE_ID_UNCERTAIN", "0.73"))  # between = ask to repeat
# below SIMILARITY_UNCERTAIN = unauthorized


_encoder = None

def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder()
        print("[VoiceID] Encoder loaded and cached", flush=True)
    return _encoder

def warmup():
    """Pre-load the encoder at startup."""
    try:
        _get_encoder()
        print("[VoiceID] Encoder pre-warmed", flush=True)
    except Exception as e:
        print(f"[VoiceID] Warmup error: {e}", flush=True)

def wav_to_float(wav_bytes: bytes) -> np.ndarray:
    """WAV bytes → float32 samples in [-1, 1], read as one sample stream.

    Stereo WAVs are deliberately NOT downmixed: the saved profiles were
    enrolled this way and the thresholds are tuned to it, so enrollment and
    recognition must keep decoding identically."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _get_embedding(audio_data: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Generate a voice embedding from audio data."""
    from resemblyzer import preprocess_wav
    import soundfile as sf
    import tempfile

    encoder = _get_encoder()

    # Save to temp wav for resemblyzer
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio_data, sample_rate)
        tmp_path = f.name

    try:
        wav = preprocess_wav(tmp_path)
        embedding = encoder.embed_utterance(wav)
        return embedding
    finally:
        os.unlink(tmp_path)


def save_profile(speaker: str, audio_data: np.ndarray, sample_rate: int = 16000):
    """Save a voice profile for a speaker."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    embedding = _get_embedding(audio_data, sample_rate)
    path = os.path.join(PROFILES_DIR, f"{speaker}.npy")
    np.save(path, embedding)
    print(f"[VoiceID] Saved profile for {speaker} → {path}", flush=True)
    return embedding


def load_profiles() -> dict:
    """Load all saved voice profiles."""
    profiles = {}
    if not os.path.exists(PROFILES_DIR):
        return profiles
    for fname in os.listdir(PROFILES_DIR):
        if fname.endswith(".npy"):
            speaker = fname[:-4]
            profiles[speaker] = np.load(os.path.join(PROFILES_DIR, fname))
    return profiles


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def identify(audio_bytes: bytes, sample_rate: int = 16000) -> dict:
    """
    Identify speaker from audio bytes.
    Returns:
        {"status": "confirmed", "speaker": "gio", "score": 0.82}
        {"status": "uncertain", "speaker": "gio", "score": 0.70}
        {"status": "unauthorized", "speaker": None, "score": 0.45}
        {"status": "no_profiles", "speaker": None, "score": 0.0}   # nobody enrolled yet
        {"status": "error", "speaker": None, "score": 0.0}         # couldn't analyze — ask to repeat
    """
    profiles = load_profiles()
    if not profiles:
        print("[VoiceID] No profiles found — skipping identification", flush=True)
        return {"status": "no_profiles", "speaker": None, "score": 0.0}

    try:
        embedding = _get_embedding(wav_to_float(audio_bytes), sample_rate)
    except Exception as e:
        # Fail closed: an analysis error must not fall through to a default speaker
        print(f"[VoiceID] Embedding error: {e}", flush=True)
        return {"status": "error", "speaker": None, "score": 0.0}

    # Compare to all profiles
    scores = {}
    for speaker, profile in profiles.items():
        scores[speaker] = cosine_similarity(embedding, profile)
        print(f"[VoiceID] {speaker}: {scores[speaker]:.3f}", flush=True)

    best_speaker = max(scores, key=scores.get)
    best_score = scores[best_speaker]

    if best_score >= SIMILARITY_CONFIRMED:
        return {"status": "confirmed", "speaker": best_speaker, "score": best_score}
    elif best_score >= SIMILARITY_UNCERTAIN:
        return {"status": "uncertain", "speaker": best_speaker, "score": best_score}
    else:
        return {"status": "unauthorized", "speaker": None, "score": best_score}
