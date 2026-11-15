"""Add synthetic live-voice demo records to the existing LAB_DATABASE."""
import os
from dotenv import load_dotenv
from app.appointments.voice_demo import ROOT, database_path, setup

if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    setup(database_path(),os.getenv("LABASSIST_WEBHOOK_SECRET",""),
          os.getenv("LAB_TIMEZONE","America/Los_Angeles"))
    print("Synthetic voice demo ready. Seed booking reference: DEMO-VOICE-001")
