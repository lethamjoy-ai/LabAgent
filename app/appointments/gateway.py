from typing import Protocol
from .models import GatewayRequest, Result
from .access import AccessContext


class AppointmentGateway(Protocol):
    access: AccessContext
    def principal(self): ...
    def execute(self, request: GatewayRequest) -> Result: ...


class DirectAppointmentGateway:
    def __init__(self, service, access: AccessContext | None = None):
        self.service = service
        self.access = access or AccessContext()

    def principal(self):
        return self.service.auth.current(self.access.session_token)

    def execute(self, request: GatewayRequest) -> Result:
        s = self.service
        try:
            actor = s.require_staff(self.access)
            if request.action == "match_patient":
                return s.match_patient(request.patient_name,request.date_of_birth)
            if request.action in ("overview","admin_overview","staff_appointments"):
                if request.target_staff_name:
                    matches = s.staff.match(request.target_staff_name)
                    if len(matches) != 1 or matches[0].id != actor.id:
                        raise PermissionError("You can view only your own schedule. An Admin can request an organization-wide overview.")
                if request.action == "staff_appointments":
                    return s.staff_appointments(self.access,request.day,request.appointment_filter)
                if not request.day:
                    raise ValueError("Specify an appointment date.")
                return s.overview(request.day,request.test_id or "CBC",self.access,
                    organization=request.action == "admin_overview",appointment_filter=request.appointment_filter)
            if request.action == "patient_appointments":
                if not request.patient_id:
                    raise ValueError("Match the patient's name and date of birth first.")
                return s.patient_appointments(request.patient_id,request.appointment_filter,request.day,request.appointment_id,self.access)
            if request.action == "patient":
                patient = s.repository.patient(request.patient_id)
                return Result(ok=bool(patient),code="patient" if patient else "not_found",patient=patient)
            if request.action == "lookup":
                with s.repository.connection() as db:
                    rows = db.execute("SELECT * FROM appointments WHERE id=? AND staff_id=? AND patient_id=?",(request.appointment_id,actor.id,request.patient_id)).fetchall()
                from .models import Appointment
                appointments = [Appointment(**dict(r)) for r in rows]
                return Result(ok=bool(appointments),code="lookup" if appointments else "not_found",display_rows=s.display_rows(appointments))
            if request.action == "availability":
                if not request.day:
                    raise ValueError("Specify a date for availability.")
                target = None
                if request.target_staff_name:
                    matches = s.staff.match(request.target_staff_name)
                    if len(matches) != 1:
                        raise ValueError("Specify one active staff member by full name.")
                    target = matches[0].id
                return s.availability_result(request.day,request.test_id or "CBC",target)
            if not request.details:
                raise ValueError("Patient, test, date and time are required.")
            if request.action == "prepare":
                return s.prepare_request(request.details,request.request_id,self.access,request.target_staff_name)
            result = s.book(request.details,request.request_id,request.confirmation_token,self.access)
            if result.ok and result.booking:
                return result.model_copy(update={"display_rows":s.display_rows([result.booking])})
            return result
        except PermissionError as exc:
            return Result(ok=False,code="access_denied",message=str(exc))
        except (ValueError,TypeError) as exc:
            return Result(ok=False,code="invalid_request",message=str(exc))
        except Exception:
            return Result(ok=False,code="tool_error",message="The appointment service failed. No booking success can be confirmed; retry the same request.")
