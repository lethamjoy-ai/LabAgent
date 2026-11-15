from typing import Protocol


class SpeechTransport(Protocol):
    def transcribe(self, audio: bytes) -> str: ...
    def synthesize(self, text: str) -> bytes: ...


class VoiceAdapter:
    """Deprecated automatic-submit stub; use the reviewed VoiceSession flow."""
    def __init__(self, assistant, speech: SpeechTransport):
        self.assistant, self.speech = assistant, speech

    def turn(self, audio, state=None):
        raise RuntimeError("Automatic voice submission is disabled. Use VoiceSession transcription, editable review, and explicit Send for general lab questions only.")
