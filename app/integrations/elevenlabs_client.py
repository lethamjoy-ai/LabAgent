"""ElevenLabs SDK setup only; constructing a client makes no API requests."""
import os
from pathlib import Path

from dotenv import load_dotenv


def create_elevenlabs_client():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise ValueError("Configure ELEVENLABS_API_KEY in .env before using ElevenLabs.")
    # Load the environment before importing the SDK; it reads its own API key.
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(timeout=30)
