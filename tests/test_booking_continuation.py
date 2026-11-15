from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.models import GatewayRequest
from app.integrations.labrag_client import MockLabRAGClient


def assistant_for(service, now=None):
    return LabAssistant(DirectAppointmentGateway(service,service.test_access),MockLabRAGClient(),now=now or service.now)


def test_dated_search_then_tomorrow_and_patient_continuation(service):
    # Reproduce the observed dates and the actual local Monday-Friday configuration.
    service.now = lambda:datetime(2026,11,7,19,tzinfo=service.timezone)
    for staff in service.staff.active():
        service.staff.replace_hours(staff.id,[0,1,2,3,4],"09:00","17:00")
    a = assistant_for(service)
    s = a.turn("Available CBC slots 2030-01-15 with Jordan.")
    assert s["availability_slots"] and s["target_staff_name"] == "Jordan"
    s = a.turn("Schedule a CBC test tomorrow at 4 PM.",s)
    assert s["requested_date"] == "2026-11-08" and s["selected_slot"] == "16:00"
    assert s["test"] == "CBC" and s["target_staff_name"] is None
    assert "November 8, 2026" in s["response"] and "4:00 PM" in s["response"]
    s = a.turn("Alex Demo 1990-01-15.",s)
    assert [e["request"]["action"] for e in s["tool_results"]] == ["match_patient","prepare"]
    args = s["tool_results"][-1]["request"]
    assert args["details"]["start"] == "2026-11-08T16:00:00-08:00"
    assert args["details"]["test_id"] == "CBC"
    assert args["details"]["staff_id"] is None and args["target_staff_name"] is None
    assert s["tool_results"][-1]["result"]["availability_reason"] == "no_shifts"
    assert "configured working hours" in s["response"] and "another date" in s["response"]
    assert "2026-11-08" in s["response"] and "4:00 PM" in s["response"]
    assert "That staff member" not in s["response"] and "No available slots" not in s["response"]
    assert not s["confirmation"]


def test_patient_turn_preserves_request_and_missing_staff_is_explicit_offer(service):
    a = assistant_for(service)
    s = a.turn("Available CBC slots 2030-01-15 with Jordan")
    s = a.turn("Schedule a CBC test tomorrow at 4 PM",s)
    assert s["requested_date"] == "2030-01-02"
    s = a.turn("Alex Demo 1990-01-15",s)
    result = s["tool_results"][-1]["result"]
    assert result["code"] == "confirmation_required"
    assert s["pending"]["start"] == "2030-01-02T16:00:00-08:00"
    assert s["pending"]["test_id"] == "CBC"
    assert s["target_staff_name"] is None
    for expected in ("No staff preference was specified", "Riley Demo", "Jordan Demo", "Alex Demo", "Complete blood count", "January 2, 2030", "4:00 PM", "confirm"):
        assert expected in s["response"]
    assert len(service.repository.appointments()) == 2  # a proposal has not booked anything
    s = a.turn("Yes, confirm.",s)
    assert "successfully scheduled" in s["response"]
    assert len(service.repository.appointments()) == 3
    a.turn("confirm",s)
    assert len(service.repository.appointments()) == 3


def test_today_tomorrow_use_lab_date_not_utc_date(service):
    # UTC January 2 is still January 1 in the lab.
    utc_clock = lambda:datetime(2030,1,2,1,tzinfo=timezone.utc)
    for word,expected in [("today","2030-01-01"),("tomorrow","2030-01-02")]:
        a = assistant_for(service,utc_clock)
        s = a.turn(f"Schedule CBC {word} at 4 PM")
        assert s["requested_date"] == expected
        assert expected in s["response"] and "America/Los_Angeles" in s["response"]


def test_missing_configuration_is_distinct_from_no_working_day(service):
    a = assistant_for(service)
    with service.repository.connection() as db:
        db.execute("DELETE FROM staff_hours")
    s = a.turn("Schedule CBC 2030-01-16 at 4 PM")
    s = a.turn("Alex Demo 1990-01-15",s)
    assert s["tool_results"][-1]["result"]["availability_reason"] == "missing_configuration"
    assert "configuration is missing" in s["response"] and "administrator" in s["response"]
    assert not s["confirmation"]


def test_selected_combination_unavailable_has_actual_alternatives(service):
    a = assistant_for(service)
    service.staff.replace_hours("STAFF2",[2],"09:00","12:00")
    s = a.turn("Schedule CBC 2030-01-16 at 4 PM with Jordan")
    s = a.turn("Alex Demo 1990-01-15",s)
    assert s["tool_results"][-1]["result"]["availability_reason"] == "selected_slot_unavailable"
    assert "Jordan Demo is unavailable" in s["response"]
    assert "4:00 PM with Riley Demo" in s["response"]
    assert "Available alternatives" in s["response"] and not s["confirmation"]
    s = a.turn("4 PM with Riley",s)
    assert s["confirmation"] and "Riley Demo" in s["response"]
    assert len(service.repository.appointments()) == 2


def test_full_date_no_open_slots_is_not_missing_configuration(service):
    # One real working window, fully occupied. Configuration exists but capacity is gone.
    with service.repository.connection() as db:
        db.execute("UPDATE staff SET is_active=0 WHERE id IN ('STAFF2','ADMIN')")
    service.staff.replace_hours("STAFF1",[2],"16:00","16:30")
    with service.repository.connection() as db:
        db.execute("INSERT INTO appointments VALUES(?,?,?,?,?,?,?)",("full","SYN002","CBC","2030-01-16T16:00:00-08:00","scheduled","full-request","STAFF1"))
    a = assistant_for(service)
    s = a.turn("Schedule CBC 2030-01-16 at 4 PM")
    s = a.turn("Alex Demo 1990-01-15",s)
    assert s["tool_results"][-1]["result"]["availability_reason"] == "no_open_slots"
    assert "no open future appointment slots" in s["response"] and "another date" in s["response"]
    assert "staff member" not in s["response"] and not s["confirmation"]
    assert "No booking" in a.turn("confirm",s)["response"]


def test_empty_explicit_availability_uses_same_explanation(service):
    with service.repository.connection() as db:
        db.execute("DELETE FROM staff_hours WHERE weekday=2")
    s = assistant_for(service).turn("Available CBC slots 2030-01-16")
    assert s["tool_results"][0]["result"]["availability_reason"] == "no_shifts"
    assert "January 16, 2030" in s["response"] and "another date" in s["response"]


def test_no_selected_staff_at_unavailable_time_does_not_blame_one(service):
    for person in service.staff.active():
        service.staff.replace_hours(person.id,[2],"09:00","12:00")
    a = assistant_for(service)
    s = a.turn("Schedule CBC 2030-01-16 at 4 PM")
    s = a.turn("Alex Demo 1990-01-15",s)
    assert "No staff are available at the requested time" in s["response"]
    assert "That staff member" not in s["response"]
    assert "9:00 AM with" in s["response"] and "Available alternatives" in s["response"]
    assert s["target_staff_name"] is None and not s["confirmation"]


def test_staff_change_after_offer_requires_new_readback(service):
    a = assistant_for(service)
    s = a.turn("Schedule CBC 2030-01-16 at 4 PM")
    s = a.turn("Alex Demo 1990-01-15",s)
    original = s["confirmation"]
    s = a.turn("with Jordan",s)
    assert s["confirmation"] != original and s["pending"]["staff_id"] == "STAFF2"
    assert "January 16, 2030" in s["response"] and "4:00 PM" in s["response"] and "Jordan Demo" in s["response"]
    assert len(service.repository.appointments()) == 2
