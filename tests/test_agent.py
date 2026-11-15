from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.models import BookingDetails


def test_missing_details_and_booking_loop(assistant, service):
    s = assistant.turn("book")
    assert "first name, last name" in s["response"] and "date of birth" in s["response"]
    s = assistant.turn("CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00", s)
    assert s["confirmation"]
    assert "Alex" not in s["response"] and "Taylor" in s["response"]
    assert [e["request"]["action"] for e in s["tool_results"]] == ["match_patient","prepare"]
    assert len(service.repository.appointments()) == 2
    s = assistant.turn("confirm", s)
    assert "successfully scheduled" in s["response"]
    first = s["response"]
    s = assistant.turn("confirm", s)
    assert "No booking" in s["response"]
    assert len(service.repository.appointments()) == 3


def test_change_requires_new_confirmation(assistant, service):
    s = assistant.turn("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00")
    old_token = s["confirmation"]
    s = assistant.turn("12:00", s)
    assert s["confirmation"] != old_token
    assert not s["confirmed"]
    assert "12:00 PM" in s["response"]
    s = assistant.turn("confirm",s)
    assert s["tool_results"][-1]["result"]["booking"]["start"].endswith("12:00:00-08:00")


def test_ambiguity_retrieval(assistant):
    s = assistant.turn("specimen blood")
    assert "Which test" in s["response"]
    s = assistant.turn("CBC",s)
    assert "SYNTHETIC MOCK" in s["response"]
    assert "Preparation: Not provided" in s["response"]
    assert "Synthetic LabAssist fixture" in s["response"]


def test_slot_lost_requires_reselection(assistant, service):
    s = assistant.turn("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00")
    d = BookingDetails(**s["pending"])
    proof = service.prepare(d,"other-person",service.test_access)
    assert service.book(d,"other-person",proof.confirmation_token,service.test_access).ok
    s = assistant.turn("confirm",s)
    assert "unavailable" in s["response"]
    assert s["confirmation"] is None and s["selected_slot"] is None
    s = assistant.turn("11:30",s)
    assert "Reply confirm" in s["response"]
    assert len(service.repository.appointments()) == 3


def test_no_confirmation_and_cancel(assistant):
    assert "No booking" in assistant.turn("confirm")["response"]
    s = assistant.turn("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00")
    s = assistant.turn("cancel",s)
    assert "No booking" in assistant.turn("confirm",s)["response"]


def test_labrag_failure(service):
    class Broken:
        def query(self,*args):
            raise TimeoutError()
    a = LabAssistant(DirectAppointmentGateway(service,service.test_access),Broken())
    s = a.turn("specimen CBC")
    assert "failed or timed out" in s["response"]
    assert s["tool_results"][0]["result"]["ok"] is False


def test_fresh_booking_cannot_inherit_old_details(assistant):
    s = assistant.turn("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00")
    s = assistant.turn("book for Alex Demo DOB 1990-01-15",s)
    assert s["confirmation"] is None
    assert "laboratory test" in s["response"]


def test_ambiguous_booking_fields_require_selection(assistant):
    for message,field in [
                          ("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 or 2030-01-16 11:00","date"),
                          ("book CBC for Taylor Example DOB 1995-06-10 on 2030-01-15 11:00 or 12:00","time")]:
        s = assistant.turn(message)
        assert f"Which {field}" in s["response"]
        assert s["confirmation"] is None
