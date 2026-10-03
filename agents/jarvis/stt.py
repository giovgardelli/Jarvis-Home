import os
import tempfile
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def transcribe(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    suffix = ".ogg" if "ogg" in mime_type else ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        with open(tmp_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file,
                language="en"
            )
        return result.text.strip()
    finally:
        os.unlink(tmp_path)