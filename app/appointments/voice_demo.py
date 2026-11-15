"""Synthetic-only live tool adapter using the existing LAB_DATABASE."""
import hashlib
import hmac
import os
import time as clock
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from .repository import Repository
from .service import AppointmentService
from .models import BookingDetails
from .access import AccessContext
from app.staff.auth import token_hash

PATIENT = "VOICE-DEMO"
STAFF = "VOICE-STAFF"
ROOT = Path(__file__).resolve().parents[2]


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    booking_reference: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class OverviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    day: Literal["today", "tomorrow"]


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["offer", "confirm"]
    test_id: Literal["CBC", "GLU", "A1C"] | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    confirmation_token: str | None = Field(default=None, min_length=20, max_length=100)
    confirmed: bool | None = None

    @model_validator(mode="after")
    def validate_step(self):
        if self.action == "offer":
            if not all((self.test_id, self.date, self.time)) or self.confirmation_token is not None or self.confirmed is not None:
                raise ValueError("Offer requires test_id, date and time only.")
            date.fromisoformat(self.date)
            time.fromisoformat(self.time)
        elif self.confirmation_token is None or self.confirmed is not True or any((self.test_id, self.date, self.time)):
            raise ValueError("Confirm requires token and confirmed=true only.")
        return self


def database_path():
    path = Path(os.getenv("LAB_DATABASE", "data/labassist.sqlite3"))
    return path.resolve()


def server_token(secret):
    return hmac.new(secret.encode(), b"labassist-synthetic-voice-v1", hashlib.sha256).hexdigest()


def setup(path, secret, timezone="America/Los_Angeles", location="Synthetic Demo Laboratory", now=None):
    """Add demo-owned records transactionally; preserve all existing records."""
    path = Path(path)
    if len(secret) < 32:
        raise ValueError("Configure the webhook secret first.")
    repo = Repository.open_existing(path) if path.exists() else Repository(path)
    service = AppointmentService(repo, timezone, now)
    with repo.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            configured = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_demo_config'").fetchone()
            if not configured:
                collision = (db.execute("SELECT 1 FROM patients WHERE id=?",(PATIENT,)).fetchone()
                    or db.execute("SELECT 1 FROM staff WHERE id=? OR username=?",(STAFF,"voice-internal")).fetchone()
                    or db.execute("SELECT 1 FROM appointments WHERE id=? OR request_id=?",("DEMO-VOICE-001","voice-seed")).fetchone())
                if collision:
                    raise ValueError("Reserved demo identifiers already exist; no records changed.")
                db.execute("INSERT INTO patients(id,name,date_of_birth) VALUES(?,?,?)", (PATIENT,"Synthetic Voice Demo","2000-01-01"))
                db.execute("INSERT INTO staff VALUES(?,?,?,?,?,?,?)", (STAFF,"voice-internal","!disabled", "Synthetic","Demo","Staff",1))
                db.execute("CREATE TABLE voice_demo_config(location TEXT NOT NULL, timezone TEXT NOT NULL)")
                db.execute("INSERT INTO voice_demo_config VALUES(?,?)", (location,timezone))
                for weekday in range(7):
                    db.execute("INSERT INTO staff_hours VALUES(?,?,?,?)", (STAFF,weekday,"09:00","17:00"))
                start = datetime.combine(service.now().date()+timedelta(days=1),time(9),service.timezone)
                # Respect existing legacy station-wide bookings as well as staff slots.
                while db.execute("SELECT 1 FROM appointments WHERE start=? AND (staff_id=? OR staff_id IS NULL)",(start.isoformat(),STAFF)).fetchone():
                    start += timedelta(minutes=30)
                    if start.hour >= 17:
                        start = datetime.combine(start.date()+timedelta(days=1),time(9),service.timezone)
                db.execute("INSERT INTO appointments(id,patient_id,test_id,start,status,request_id,staff_id) VALUES(?,?,?,?,?,?,?)",
                           ("DEMO-VOICE-001",PATIENT,"CBC",start.isoformat(),"scheduled","voice-seed",STAFF))
            else:
                config = db.execute("SELECT * FROM voice_demo_config").fetchone()
                if not config or not db.execute("SELECT 1 FROM patients WHERE id=?",(PATIENT,)).fetchone() or not db.execute("SELECT 1 FROM staff WHERE id=?",(STAFF,)).fetchone():
                    raise ValueError("Demo configuration is incomplete; no records changed.")
                location, timezone = config["location"], config["timezone"]
            # Explicit setup also renews this demo-only server credential after rotation.
            db.execute("DELETE FROM staff_sessions WHERE staff_id=?",(STAFF,))
            db.execute("INSERT INTO staff_sessions VALUES(?,?,?,?)", (token_hash(server_token(secret)),STAFF,clock.time()+86400*365,clock.time()))
            db.commit()
        except Exception:
            db.rollback()
            raise
    service = AppointmentService(repo,timezone,now)
    return VoiceAppointments(service, AccessContext(server_token(secret)), location)


def configured(secret):
    path = database_path()
    if not path.is_file():
        raise RuntimeError("Voice demo database is not provisioned.")
    # Existing schema only: do not run repository migrations during a lookup.
    repo = Repository.open_existing(path)
    with repo.connection() as db:
        config = db.execute("SELECT * FROM voice_demo_config").fetchone()
    if not config:
        raise RuntimeError("Voice demo configuration missing.")
    return VoiceAppointments(AppointmentService(repo,config["timezone"]),AccessContext(server_token(secret)),config["location"])


class VoiceAppointments:
    def __init__(self, service, access, location):
        self.service, self.access, self.location = service, access, location

    def projection(self, start, test_id, reference=None, status=None):
        local = start.astimezone(self.service.timezone)
        return dict(booking_reference=reference,test_id=test_id,date=local.date().isoformat(),
                    time=local.strftime("%H:%M"),timezone=self.service.timezone.key,
                    utc_offset=local.strftime("%z"),location=self.location,status=status)

    def lookup(self, reference):
        self.service.require_staff(self.access)
        with self.service.repository.connection() as db:
            row = db.execute("SELECT * FROM appointments WHERE id=? AND patient_id=? AND staff_id=?",
                             (reference,PATIENT,STAFF)).fetchone()
        if not row:
            return {"status":"not_found","message":"No synthetic demo booking matches that exact reference."}
        return {"status":"found","appointment":self.projection(datetime.fromisoformat(row["start"]),row["test_id"],row["id"],row["status"])}

    def overview(self, day):
        resolved = self.service.now().date() + timedelta(days=1 if day == "tomorrow" else 0)
        result = self.service.overview(resolved,access=self.access,organization=False,appointment_filter="all")
        # Both staff authorization (service) and demo identity (adapter) apply.
        records = [a for a in result.appointments if a.patient_id == PATIENT and a.staff_id == STAFF]
        appointments = [self.projection(a.start,a.test_id,a.id,a.status) for a in records]
        count = len(appointments)
        return {"status":"found" if count else "empty","date":resolved.isoformat(),
                "timezone":self.service.timezone.key,"count":count,"appointments":appointments,
                "message":f"{count} synthetic demo appointment(s) on {resolved.isoformat()}." if count else
                    f"No synthetic demo appointments on {resolved.isoformat()}."}

    def alternatives(self, day, test_id):
        slots = self.service.availability_result(day,test_id,STAFF)
        return {"status":"unavailable","reason":"selected_slot_unavailable" if slots.slots else slots.availability_reason,
                "alternatives":[self.projection(s.start,test_id) for s in slots.slots],
                "message":"Choose an offered alternative, or ask for another date. A new offer and confirmation are required."}

    def create(self, body):
        self.service.require_staff(self.access)
        if body.action == "offer":
            start = datetime.combine(date.fromisoformat(body.date),time.fromisoformat(body.time),self.service.timezone)
            details = BookingDetails(patient_id=PATIENT,staff_id=STAFF,test_id=body.test_id,start=start)
            if start not in {s.start for s in self.service.availability(start.date(),body.test_id,STAFF)}:
                return self.alternatives(start.date(),body.test_id)
            result = self.service.prepare(details,"voice-"+uuid4().hex,self.access)
            if not result.ok:
                return self.alternatives(start.date(),body.test_id)
            return {"status":"confirmation_required","appointment":self.projection(start,body.test_id),
                    "confirmation_token":result.confirmation_token,"expires_in_seconds":600,
                    "message":"Read back test, date, time, timezone and location. Wait for a clear yes before confirming. Nothing has been booked."}
        with self.service.repository.connection() as db:
            proof = db.execute("SELECT * FROM confirmations WHERE token=?",(body.confirmation_token,)).fetchone()
        if not proof:
            return {"status":"invalid_confirmation","message":"Request a fresh offer and explicit confirmation."}
        details = BookingDetails.model_validate_json(proof["details"])
        if details.patient_id != PATIENT or details.staff_id != STAFF or not proof["request_id"].startswith("voice-"):
            return {"status":"invalid_confirmation"}
        try:
            # Appointment and its unique request_id are the durable confirmation result,
            # committed together by book(). It revalidates proof/session under BEGIN IMMEDIATE.
            result = self.service.book(details,proof["request_id"],body.confirmation_token,self.access)
        except ValueError:
            return {"status":"expired_confirmation","message":"Offer expired or invalid. Request a fresh offer and confirm again."}
        if not result.ok:
            return self.alternatives(details.start.date(),details.test_id)
        a = result.booking
        return {"status":"confirmed","appointment":self.projection(a.start,a.test_id,a.id,a.status)}
