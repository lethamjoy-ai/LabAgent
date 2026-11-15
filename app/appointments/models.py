from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

TESTS = {"CBC": "Complete blood count", "GLU": "Glucose", "A1C": "Hemoglobin A1c"}


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Patient(Model):
    id: str
    name: str
    date_of_birth: date | None = None
    gender: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None

    def age_on(self, today: date) -> int | None:
        if self.date_of_birth is None:
            return None
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def age(self) -> int | None:
        return self.age_on(date.today())


class AppointmentDisplay(Model):
    patient_name: str
    date_of_birth: date | None = None
    test_name: str
    start: datetime
    status: str
    staff_name: str | None = None


class Slot(Model):
    start: datetime
    appointment_type: str
    staff_id: str | None = None
    staff_name: str | None = None


class BookingDetails(Model):
    patient_id: str
    test_id: str
    start: datetime
    staff_id: str | None = None


class Appointment(BookingDetails):
    id: str
    status: str
    request_id: str


class GatewayRequest(Model):
    action: Literal["patient", "match_patient", "patient_appointments", "staff_appointments", "overview", "admin_overview", "availability", "lookup", "prepare", "book"]
    patient_id: str | None = None
    test_id: str | None = None
    day: date | None = None
    appointment_id: str | None = None
    details: BookingDetails | None = None
    request_id: str | None = None
    confirmation_token: str | None = None
    patient_name: str | None = None
    date_of_birth: date | None = None
    appointment_filter: Literal["all", "upcoming", "historical", "completed"] = "all"
    target_staff_name: str | None = None


class Result(Model):
    ok: bool
    code: str
    message: str = ""
    patient: Patient | None = None
    appointments: list[Appointment] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    booking: Appointment | None = None
    confirmation_token: str | None = None
    display_rows: list[AppointmentDisplay] = Field(default_factory=list)
    selected_details: BookingDetails | None = None
    availability_reason: Literal["available", "missing_configuration", "no_shifts", "no_open_slots", "selected_slot_unavailable"] | None = None
