"""Bounded, server-only ElevenLabs calls. Never log audio/provider payloads."""
from dataclasses import dataclass
from io import BytesIO
import os
import re
import wave
from pathlib import Path
from dotenv import load_dotenv
from .elevenlabs_client import create_elevenlabs_client


class VoiceError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceSettings:
    voice_id: str
    stt_model: str = "scribe_v2"
    tts_model: str = "eleven_multilingual_v2"

    @classmethod
    def configured(cls):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        if not os.getenv("ELEVENLABS_API_KEY") or not os.getenv("ELEVENLABS_VOICE_ID"):
            raise VoiceError("Voice needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in server configuration. Text chat is available.")
        return cls(os.environ["ELEVENLABS_VOICE_ID"],os.getenv("ELEVENLABS_STT_MODEL","scribe_v2"),
                   os.getenv("ELEVENLABS_TTS_MODEL","eleven_multilingual_v2"))


def spoken_excerpt(answer):
    """Extract complete sentences; do not invent a model-generated summary."""
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)",r"\1",answer)
    text = re.sub(r"https?://\S+|www\.\S+|\[\d+(?:\s*,\s*\d+)*\]","",text)
    # Sources/diagnostic appendices are not spoken, even if returned in answer.
    text = re.split(r"\n\s*(?:Sources|References|Request details|Diagnostics)\s*:",text,flags=re.I)[0]
    text = re.sub(r"(?im)^\s*(?:request[_ ]?id|http[_ ]?status|endpoint|status)\s*:.*$","",text)
    text = " ".join(re.sub(r"[*#`_]","",text).split())
    if len(text) <= 600:
        return text
    sentences = re.split(r"(?<=[.!?])\s+",text)
    excerpt = ""
    for sentence in sentences:
        if len(excerpt)+len(sentence)+1 > 480:
            break
        excerpt += (" " if excerpt else "") + sentence
    if not excerpt:
        return "The full laboratory answer and sources are available in chat. Please read them before using the instructions."
    return "Short excerpt. " + excerpt + " Read the full answer and sources in chat."


class ElevenLabsVoice:
    def __init__(self, settings=None, client=None):
        self.settings = settings or VoiceSettings.configured()
        self.client = client or create_elevenlabs_client()

    def transcribe(self, recording):
        if not recording or len(recording) > 2_000_000:
            raise VoiceError("Record a short question of up to 30 seconds, or use text chat.")
        try:
            with wave.open(BytesIO(recording)) as audio:
                duration = audio.getnframes()/audio.getframerate()
                if not 0.1 <= duration <= 30:
                    raise VoiceError("Record between 0.1 and 30 seconds, or use text chat.")
        except (wave.Error,EOFError,ZeroDivisionError):
            raise VoiceError("The recording is empty or unreadable. Record again or use text chat.") from None
        try:
            result = self.client.speech_to_text.convert(file=BytesIO(recording),
                model_id=self.settings.stt_model,language_code="eng",tag_audio_events=False,
                diarize=False,request_options={"max_retries":0})
            text = result.text.strip()
            if not text or len(text) > 3000:
                raise VoiceError("No usable transcript was returned. Record again or use text chat.")
            return text
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("Transcription failed. Record again or use text chat; no question was sent.") from None

    def speak(self, text):
        if not text:
            raise VoiceError("No spoken answer is available. Read the answer in chat.")
        try:
            chunks = self.client.text_to_speech.convert(voice_id=self.settings.voice_id,
                model_id=self.settings.tts_model,text=text,output_format="mp3_44100_128",
                request_options={"max_retries":0})
            audio = bytearray()
            for chunk in chunks:
                audio.extend(chunk)
                if len(audio) > 5_000_000:
                    raise VoiceError("Audio playback is unavailable. The text answer is ready.")
            if not audio:
                raise VoiceError("Audio playback is unavailable. The text answer is ready.")
            return bytes(audio)
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("Audio generation failed. The text answer and sources are available.") from None
