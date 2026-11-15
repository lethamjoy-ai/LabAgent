import secrets
import sqlite3
import time as clock
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from .models import Appointment, AppointmentDisplay, BookingDetails, Result, Slot, TESTS
from .presentation import natural_date, natural_time
from app.staff.auth import AuthService, token_hash
from app.staff.repository import StaffRepository


class AppointmentService:
    def __init__(self, repository, timezone="America/Los_Angeles", now=None):
        self.repository = repository
        self.timezone = ZoneInfo(timezone)
        clock = now or (lambda: datetime.now(self.timezone))
        self.now = lambda: clock().astimezone(self.timezone)
        self.staff = StaffRepository(repository)
        self.auth = AuthService(repository)

    def require_staff(self, access):
        actor = self.auth.current(access.session_token if access else None)
        if not actor:
            raise PermissionError("Please sign in with an active staff account. Your session may have expired.")
        return actor

    def validate(self, details):
        if not self.repository.patient(details.patient_id):
            raise ValueError("The patient record could not be found.")
        if details.test_id not in TESTS:
            raise ValueError("Choose CBC, GLU, or A1C for an appointment.")
        if details.start.tzinfo is None or details.start.utcoffset() is None:
            raise ValueError("An explicit laboratory timezone is required.")
        local = details.start.astimezone(self.timezone)
        if local <= self.now():
            raise ValueError("Choose a future slot.")
        if local.hour < 9 or local.hour >= 17 or local.minute not in (0,30) or local.second or local.microsecond:
            raise ValueError("Choose a 30 minute slot between 09:00 and 16:30.")
        staff = self.staff.get(details.staff_id)
        if not staff or not staff.is_active:
            raise ValueError("Select an active staff member for this appointment.")
        hours = self.staff.working_hours(staff.id, local.weekday())
        if not hours or not (time.fromisoformat(hours[0]) <= local.time() and
                (local + timedelta(minutes=30)).time() <= time.fromisoformat(hours[1])):
            raise ValueError("The selected staff member is outside their configured working hours.")
        return details.model_copy(update={"start":local})

    def availability(self, day, test_id, staff_id=None):
        if test_id not in TESTS:
            raise ValueError("Specify appointment type: CBC, GLU, or A1C.")
        staff = [p for p in self.staff.active() if not staff_id or p.id == staff_id]
        slots = []
        start = datetime.combine(day,time(9),self.timezone)
        with self.repository.connection() as db:
            occupied = [(r["start"],r["staff_id"]) for r in db.execute("SELECT start,staff_id FROM appointments")]
        occupied = [(datetime.fromisoformat(s),owner) for s,owner in occupied]
        for person in staff:
            hours = self.staff.working_hours(person.id,day.weekday())
            if not hours:
                continue
            for i in range(16):
                s = start + timedelta(minutes=30*i)
                if (s > self.now() and time.fromisoformat(hours[0]) <= s.time() and
                        (s+timedelta(minutes=30)).time() <= time.fromisoformat(hours[1]) and
                        not any(t == s and (owner is None or owner == person.id) for t,owner in occupied)):
                    slots.append(Slot(start=s,appointment_type=test_id,staff_id=person.id,staff_name=person.name))
        return sorted(slots,key=lambda s:(s.start,s.staff_name))

    def overview(self, day, test_id="CBC", access=None, organization=False, appointment_filter="all"):
        actor = self.require_staff(access)
        if organization and actor.role != "Admin":
            raise PermissionError("An Admin account is required for an organization-wide overview.")
        with self.repository.connection() as db:
            if organization:
                rows = db.execute("SELECT * FROM appointments ORDER BY start").fetchall()
            else:
                rows = db.execute("SELECT * FROM appointments WHERE staff_id=? ORDER BY start",(actor.id,)).fetchall()
        appointments = [Appointment(**dict(r)) for r in rows]
        appointments = self.filter_appointments(appointments,day,appointment_filter)
        return Result(ok=True,code="overview",appointments=appointments,display_rows=self.display_rows(appointments))

    def availability_result(self, day, test_id, staff_id=None):
        """Explain an empty result using configuration, not appointment counts."""
        slots = self.availability(day,test_id,staff_id)
        people = [p for p in self.staff.active() if not staff_id or p.id == staff_id]
        label = f"{natural_date(day)} ({day.isoformat()}, {self.timezone.key})"
        if slots:
            return Result(ok=True,code="availability",slots=slots,availability_reason="available",
                message=f"Available appointment slots for {label}.")
        configured = [p for p in people if any(self.staff.working_hours(p.id,i) for i in range(7))]
        on_day = [p for p in people if self.staff.working_hours(p.id,day.weekday())]
        if not configured:
            reason, message = "missing_configuration", f"Availability configuration is missing for the active staff considered for {label}. Ask an administrator to configure working hours, or check another date."
        elif not on_day:
            reason, message = "no_shifts", f"No staff considered for this request have configured working hours on {label}. Would you like to check another date?"
        else:
            reason, message = "no_open_slots", f"There are no open future appointment slots on {label} within the configured working hours. Would you like to check another date?"
        return Result(ok=True,code="availability",availability_reason=reason,message=message)

    def prepare_request(self, details, request_id, access, target_staff_name=None):
        """Select or explicitly offer an assignee through the reusable domain layer."""
        actor = self.require_staff(access)
        if details.start.tzinfo is None:
            raise ValueError("An explicit laboratory timezone is required.")
        details = details.model_copy(update={"start":details.start.astimezone(self.timezone)})
        target = details.staff_id
        if target_staff_name:
            matches = self.staff.match(target_staff_name)
            if len(matches) != 1:
                raise ValueError("Specify one active staff member by full name.")
            target = matches[0].id
        details = details.model_copy(update={"staff_id":target})
        exact = [slot for slot in self.availability(details.start.date(),details.test_id,target) if slot.start == details.start]
        if not exact:
            return self.unavailable(details)
        offered = next((slot for slot in exact if slot.staff_id == actor.id),exact[0])
        selected = details.model_copy(update={"staff_id":offered.staff_id})
        result = self.prepare(selected,request_id,access)
        if result.ok and not target:
            names = ", ".join(slot.staff_name for slot in exact)
            result = result.model_copy(update={"message":f"No staff preference was specified. Available at the requested time: {names}. I can offer {offered.staff_name}. " + result.message})
        return result.model_copy(update={"selected_details":selected})

    def display_rows(self, appointments):
        rows = []
        for a in sorted(appointments,key=lambda a:a.start):
            p = self.repository.patient(a.patient_id)
            staff = self.staff.get(a.staff_id) if a.staff_id else None
            rows.append(AppointmentDisplay(patient_name=p.name if p else "Not available",
                date_of_birth=p.date_of_birth if p else None,test_name=TESTS.get(a.test_id,"Laboratory test"),
                start=a.start,status=a.status,staff_name=staff.name if staff else None))
        return rows

    def staff_appointments(self, access=None, day=None, appointment_filter="upcoming"):
        """Staff-scoped retrieval across dates; does not calculate availability."""
        actor = self.require_staff(access)
        with self.repository.connection() as db:
            records = db.execute("SELECT * FROM appointments WHERE staff_id=? ORDER BY start",(actor.id,)).fetchall()
        appointments = self.filter_appointments([Appointment(**dict(r)) for r in records],day,appointment_filter)
        return Result(ok=True,code="staff_appointments",display_rows=self.display_rows(appointments))

    def match_patient(self, name, dob):
        if not name or not dob:
            return Result(ok=False,code="identity_required",message="What is the patient's first name, last name, and date of birth?")
        if dob > self.now().date():
            raise ValueError("Date of birth cannot be in the future.")
        matches = self.repository.match_patients(name,dob)
        if not matches:
            return Result(ok=False,code="identity_not_found",message="No patient matches that full name and date of birth. Check the details or use authorized demographic maintenance.")
        if len(matches) != 1:
            return Result(ok=False,code="identity_not_unique",message="Multiple patient records match that full name and date of birth. Ask authorized staff to resolve the duplicate records before booking.")
        return Result(ok=True,code="patient",patient=matches[0])

    def filter_appointments(self, appointments, day, appointment_filter):
        if day:
            appointments = [a for a in appointments if a.start.astimezone(self.timezone).date() == day]
        if appointment_filter == "upcoming":
            appointments = [a for a in appointments if a.start > self.now() and a.status == "scheduled"]
        elif appointment_filter == "historical":
            appointments = [a for a in appointments if a.start <= self.now()]
        elif appointment_filter == "completed":
            appointments = [a for a in appointments if a.status == "completed"]
        return appointments

    def patient_appointments(self, patient_id, appointment_filter="all", day=None, appointment_id=None, access=None):
        actor = self.require_staff(access)
        with self.repository.connection() as db:
            rows = db.execute("SELECT * FROM appointments WHERE patient_id=? AND staff_id=? ORDER BY start",(patient_id,actor.id)).fetchall()
        appointments = self.filter_appointments([Appointment(**dict(r)) for r in rows],day,appointment_filter)
        if appointment_id:
            appointments = [a for a in appointments if a.id == appointment_id]
        return Result(ok=True,code="patient_appointments",display_rows=self.display_rows(appointments))

    def prepare(self, details, request_id, access=None):
        actor = self.require_staff(access)
        details = self.validate(details)
        if not request_id:
            raise ValueError("A booking request is required.")
        if details.start not in {s.start for s in self.availability(details.start.date(),details.test_id,details.staff_id)}:
            return self.unavailable(details)
        token = secrets.token_urlsafe(32)
        with self.repository.connection() as db:
            previous = db.execute("SELECT actor_staff_id,session_hash FROM confirmations WHERE request_id=?",(request_id,)).fetchone()
            if previous and (previous["actor_staff_id"] != actor.id or previous["session_hash"] != token_hash(access.session_token)):
                raise PermissionError("This booking request belongs to another staff session.")
            db.execute("""INSERT INTO confirmations(token,details,request_id,expires,actor_staff_id,session_hash) VALUES(?,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET token=excluded.token,details=excluded.details,expires=excluded.expires""",
                (token,details.model_dump_json(),request_id,clock.time()+600,actor.id,token_hash(access.session_token)))
        patient = self.repository.patient(details.patient_id)
        staff = self.staff.get(details.staff_id)
        return Result(ok=True,code="confirmation_required",confirmation_token=token,
            message=f"I found {patient.name}'s record. {staff.name} is available for {TESTS[details.test_id]} on "
                f"{natural_date(details.start)} at {natural_time(details.start)} ({self.timezone.key}). "
                "Would you like to confirm? Reply confirm or cancel. Confirmation expires in 10 minutes.")

    def unavailable(self, details):
        local = details.start.astimezone(self.timezone)
        all_staff = self.availability_result(local.date(),details.test_id)
        staff = self.staff.get(details.staff_id) if details.staff_id else None
        requested = f"{natural_date(local)} ({local.date().isoformat()}) at {natural_time(local)} ({self.timezone.key})"
        if not all_staff.slots:
            message = f"Requested appointment: {requested}. {all_staff.message}"
            reason = all_staff.availability_reason
        else:
            subject = f"{staff.name} is unavailable" if staff else "No staff are available"
            message = f"{subject} at the requested time, {requested}. Select an alternative staff member and time, then confirm again."
            if staff and not any(self.staff.working_hours(staff.id,i) for i in range(7)):
                message = f"Working-hours configuration is missing for {staff.name}. Requested appointment: {requested}. Select an available alternative and confirm again."
            reason = "selected_slot_unavailable"
        return Result(ok=False,code="unavailable",message=message,slots=all_staff.slots,availability_reason=reason)

    def book(self, details, request_id, token, access=None):
        actor = self.require_staff(access)
        if not token or not request_id:
            return Result(ok=False,code="confirmation_required",message="Review the exact appointment details and obtain explicit confirmation first.")
        if details.start.tzinfo is None:
            raise ValueError("Timezone required.")
        details = details.model_copy(update={"start":details.start.astimezone(self.timezone)})
        with self.repository.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # Revalidate login under the same write transaction; revoked/expired sessions cannot commit.
                valid_session = db.execute("""SELECT 1 FROM staff_sessions JOIN staff ON staff.id=staff_sessions.staff_id
                    WHERE token_hash=? AND staff.id=? AND expires>? AND is_active=1""",
                    (token_hash(access.session_token),actor.id,self.auth.now())).fetchone()
                if not valid_session:
                    raise PermissionError("Staff session expired. Please sign in again.")
                previous = db.execute("SELECT * FROM appointments WHERE request_id=?",(request_id,)).fetchone()
                proof = db.execute("SELECT * FROM confirmations WHERE token=? AND request_id=?",(token,request_id)).fetchone()
                valid = (proof and proof["details"] == details.model_dump_json() and proof["actor_staff_id"] == actor.id
                    and proof["session_hash"] == token_hash(access.session_token))
                if previous:
                    a = Appointment(**dict(previous))
                    prior = BookingDetails(**a.model_dump(include={"patient_id","test_id","start","staff_id"}))
                    if valid and prior == details:
                        db.commit()
                        return Result(ok=True,code="booked",booking=a)
                    raise ValueError("This booking request has already been used. Review a new request.")
                if not valid or proof["expires"] < clock.time():
                    raise ValueError("Confirmation missing, expired, or does not match this session and appointment. Review the details again.")
                details = self.validate(details)
                if db.execute("SELECT 1 FROM appointments WHERE start=? AND (staff_id=? OR staff_id IS NULL)",
                        (details.start.isoformat(),details.staff_id)).fetchone():
                    db.rollback()
                    return self.unavailable(details)
                appointment = Appointment(**details.model_dump(),id="LAB-"+uuid4().hex[:12],status="scheduled",request_id=request_id)
                db.execute("INSERT INTO appointments(id,patient_id,test_id,start,status,request_id,staff_id) VALUES(?,?,?,?,?,?,?)",
                    (appointment.id,details.patient_id,details.test_id,details.start.isoformat(),appointment.status,request_id,details.staff_id))
                db.commit()
                return Result(ok=True,code="booked",booking=appointment)
            except sqlite3.IntegrityError:
                db.rollback()
                return self.unavailable(details)
            except Exception:
                db.rollback()
                raise
