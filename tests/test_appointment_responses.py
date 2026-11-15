from tests.mock_router import OfflineRouter
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.models import GatewayRequest, AppointmentDisplay
from app.appointments.presentation import appointment_table, natural_time
from app.agents.lab_assistant import LabAssistant
from app.agents.parsing import requested_time
from app.integrations.labrag_client import MockLabRAGClient
from tests.test_ui import ui_for, sign_in


def add_record(service, identifier, start, staff="STAFF1", status="scheduled"):
    # Synthetic isolated fixtures only; never touch the application database.
    with service.repository.connection() as db:
        db.execute("INSERT INTO appointments VALUES(?,?,?,?,?,?,?)",
            (identifier,"SYN003","A1C",start,status,"req-"+identifier,staff))


@pytest.mark.parametrize("question,intent",[
    ("Show my upcoming appointments.","upcoming"),
    ("Show my daily schedule today.","overview"),
    ("Show my available appointment slots tomorrow.","availability"),
    ("Available slots tomorrow", "availability"),
    ("Organization-wide daily overview", "admin_overview"),
])
def test_distinct_routing(question,intent):
    assert OfflineRouter.route(question) == intent


def test_upcoming_is_staff_scoped_across_dates_without_availability(service,assistant,monkeypatch):
    add_record(service,"own-later","2030-02-01T10:00:00-08:00")
    add_record(service,"other-staff","2030-02-01T11:00:00-08:00",staff="STAFF2")
    add_record(service,"past","2029-12-31T10:00:00-08:00")
    add_record(service,"already-done","2030-02-02T10:00:00-08:00",status="completed")
    add_record(service,"cancelled","2030-02-03T10:00:00-08:00",status="cancelled")
    def unexpected(*args,**kwargs):
        raise AssertionError("Appointment retrieval must not call availability")
    monkeypatch.setattr(service,"availability",unexpected)
    s = assistant.turn("Show my upcoming appointments.")
    assert s["intent"] == "upcoming"
    assert s["tool_results"][0]["request"]["action"] == "staff_appointments"
    assert s["tool_results"][0]["request"]["day"] is None
    assert len(s["appointment_rows"]) == 2
    assert "2 upcoming scheduled laboratory appointments" in s["response"]
    assert [r["Appointment Date"] for r in s["appointment_rows"]] == ["January 15, 2030","February 1, 2030"]
    assert all(r["Status"] == "Scheduled" for r in s["appointment_rows"])
    assert set(s["appointment_rows"][0]) == {"Patient Name","Date of Birth","Laboratory Test","Appointment Date","Appointment Time","Status"}
    assert not s["available_slots"] and not s["availability_slots"]
    message = s["history"][-1]
    assert "availability_slots" not in message and "overview_rows" not in message
    for hidden in ("STAFF1","SYN003","own-later","req-","Available Appointment Slots"):
        assert hidden not in str(message)
    # Even Admin's upcoming list is their own, not the organization.
    admin = DirectAppointmentGateway(service,service.admin_access)
    result = admin.execute(GatewayRequest(action="staff_appointments",appointment_filter="upcoming"))
    assert result.ok and not result.display_rows


def test_upcoming_date_followup_and_empty_summary(assistant):
    s = assistant.turn("Show my schedule 2030-01-15")
    s = assistant.turn("Show my upcoming appointments",s)
    assert s["tool_results"][0]["request"]["day"] is None
    s = assistant.turn("2030-01-16",s)
    assert s["appointment_rows"] == []
    assert "0 upcoming" in s["response"] and "on 2030-01-16" in s["response"]
    assert "availability_slots" not in s["history"][-1]


def test_switching_from_booking_does_not_treat_schedule_date_as_dob(assistant):
    s = assistant.turn("Schedule CBC tomorrow at 11 AM")
    assert s["awaiting_identity"]
    s = assistant.turn("Show my upcoming appointments",s)
    assert not s["awaiting_identity"]
    s = assistant.turn("2030-01-16",s)
    assert s["tool_results"][0]["request"]["day"] == "2030-01-16"
    assert "0 upcoming" in s["response"]


def test_daily_schedule_is_selected_date_not_all_future(assistant,service,monkeypatch):
    add_record(service,"next-day","2030-01-16T10:00:00-08:00")
    monkeypatch.setattr(service,"availability",lambda *a,**kw: pytest.fail("Daily schedule must not calculate unrelated availability"))
    s = assistant.turn("Show my daily schedule 2030-01-15")
    assert "2 appointments" in s["response"]
    assert len(s["overview_rows"]) == 2 and not s["overview_slots"]
    assert "availability_slots" not in s["history"][-1]
    assert "appointment_rows" not in s["history"][-1]


def test_explicit_availability_respects_shift_and_bookings(assistant,service):
    service.staff.replace_hours("STAFF2",[1],"09:00","11:00")  # Tuesday
    add_record(service,"occupied","2030-01-15T09:30:00-08:00",staff="STAFF2")
    s = assistant.turn("Available CBC slots 2030-01-15 with Jordan")
    # 09:00 and 10:00 belong to Riley, so Jordan can work those same times.
    assert s["availability_slots"] == ["9:00 AM - Jordan Demo","10:00 AM - Jordan Demo","10:30 AM - Jordan Demo"]
    assert "availability_slots" in s["history"][-1]
    assert "overview_rows" not in s["history"][-1] and "appointment_rows" not in s["history"][-1]
    s = assistant.turn("Available slots 2030-01-16 with Jordan",s)
    assert s["availability_slots"] == []


@pytest.mark.parametrize("raw,expected_date,expected_time",[
    ("2030-01-15T10:00:00-08:00","January 15, 2030","10:00 AM"),
    ("2030-01-15T22:00:00-08:00","January 15, 2030","10:00 PM"),
    ("2030-01-16T06:00:00+00:00","January 15, 2030","10:00 PM"),
    ("2030-01-15T18:00:00+00:00","January 15, 2030","10:00 AM"),
    ("2030-07-15T17:00:00+00:00","July 15, 2030","10:00 AM"),
])
def test_local_date_and_time_formatting(raw,expected_date,expected_time):
    row = AppointmentDisplay(patient_name="Synthetic Patient",test_name="CBC",status="scheduled",start=datetime.fromisoformat(raw))
    public = appointment_table([row],ZoneInfo("America/Los_Angeles"))[0]
    assert public["Appointment Date"] == expected_date
    assert public["Appointment Time"] == expected_time


def test_am_pm_parser_does_not_swap_morning_evening():
    assert requested_time("at 10 AM") == "10:00"
    assert requested_time("at 10 PM") == "22:00"
    assert requested_time("at 22:00") == "22:00"
    assert natural_time(datetime(2030,1,15,22)) == "10:00 PM"


def test_upcoming_gateway_cannot_view_another_staff(service):
    gateway = DirectAppointmentGateway(service,service.test_access)
    assert gateway.execute(GatewayRequest(action="staff_appointments",target_staff_name="Jordan",appointment_filter="upcoming")).code == "access_denied"
    assert gateway.execute(GatewayRequest(action="admin_overview",day="2030-01-15")).code == "access_denied"
    assert DirectAppointmentGateway(service).execute(GatewayRequest(action="staff_appointments")).code == "access_denied"


def test_streamlit_upcoming_has_date_and_no_availability(service,monkeypatch):
    add_record(service,"far-future","2099-01-15T10:00:00-08:00")
    add_record(service,"other-future","2099-01-15T11:00:00-08:00",staff="STAFF2")
    ui = sign_in(ui_for(service,monkeypatch))
    ui.chat_input[0].set_value("Show my upcoming appointments.").run(timeout=30)
    assert not ui.exception
    table = ui.dataframe[0].value
    assert "Appointment Date" in table.columns
    far = table[table["Appointment Date"] == "January 15, 2099"]
    assert len(far) == 1 and far.iloc[0]["Appointment Time"] == "10:00 AM"
    assert not any("Available Appointment Slots" in m.value for m in ui.markdown)
    assert not any("No available slots" in item.value for item in ui.text)
    ui.chat_input[0].set_value("Available CBC slots 2099-01-15 with Jordan").run(timeout=30)
    assert not ui.exception
    assert any("Available Appointment Slots" in m.value for m in ui.markdown)
    assert all("11:00 AM" not in slot for slot in ui.session_state["conversation"]["availability_slots"])
    assert any("10:00 AM" in slot for slot in ui.session_state["conversation"]["availability_slots"])
