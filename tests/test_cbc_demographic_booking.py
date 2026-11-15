from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from app.agents.parsing import extract_identity
from app.agents.planner import Plan
from tests.test_llm_intent_router import RecordingModel, assistant_for


LABELLED = "First name: Jordan; Last name: Sample; Date of birth: 1988-04-20"
BOOKING = "Schedule CBC on 2026-11-12 at 1:00 PM"


def setup_booking(service):
    service.now = lambda: datetime(2026,11,8,12,tzinfo=ZoneInfo("America/Los_Angeles"))
    model = RecordingModel(Plan(intent="booking",fresh_booking=True))
    return assistant_for(service,model), model


def assert_proposal(s, service, before):
    assert s["confirmation"]
    assert s["patient"] == s["pending"]["patient_id"] == "SYN002"
    assert s["pending"]["test_id"] == "CBC"
    assert s["pending"]["start"] == "2026-11-12T13:00:00-08:00"
    assert len(service.repository.appointments()) == before


@pytest.mark.parametrize("text,awaiting", [
    (LABELLED,False),
    ("First-name: Jordan Last-name: Sample DOB: 1988-04-20",False),
    ("Last name: Sample\nFirst name: Jordan\nDOB: 1988-04-20",False),
    ("Jordan Sample 1988-04-20",True),
    ("Jordan, Sample, 1988-04-20",True),
    ("Jordan Sample, April 20, 1988",True),
    (BOOKING + "; " + LABELLED,False),
    ("Schedule CBC for Jordan Sample DOB 1988-04-20 on 2026-11-12 at 1 PM",False),
])
def test_identity_formats(text,awaiting):
    name,dob=extract_identity(text,awaiting=awaiting)
    assert name=="Jordan Sample" and dob==date(1988,4,20)


def test_complete_labelled_booking_merges_before_lookup(service):
    a,model=setup_booking(service)
    before=len(service.repository.appointments())
    s=a.turn(BOOKING + "; " + LABELLED)
    assert len(model.calls)==1
    assert [e["request"]["action"] for e in s["tool_results"]]==["match_patient","prepare"]
    assert_proposal(s,service,before)
    # Separate labels must be redacted even on the first turn.
    for private in ("Jordan","Sample","1988-04-20"):
        assert private not in str(model.calls)
    model.result=Plan(intent="continue")
    s=a.turn("with Riley",s)
    assert_proposal(s,service,before)
    model.result=Plan(intent="confirm")
    rejected=a.turn("Please finalize this",s)
    assert len(service.repository.appointments())==before
    model.result=Plan(intent="booking",fresh_booking=True)
    s=a.turn(BOOKING + "; " + LABELLED,rejected)
    model.result=Plan(intent="confirm")
    saved=a.turn("confirm",s)
    assert saved["tool_results"][-1]["result"]["code"]=="booked"
    assert len(service.repository.appointments())==before+1


@pytest.mark.parametrize("reply",[LABELLED,"Jordan Sample 1988-04-20","Jordan, Sample, 1988-04-20"])
def test_two_turn_booking_keeps_fields_and_matched_id(service,reply):
    a,model=setup_booking(service)
    before=len(service.repository.appointments())
    s=a.turn(BOOKING)
    assert (s["test"],s["requested_date"],s["selected_slot"])==("CBC","2026-11-12","13:00")
    assert not s.get("confirmation")
    model.result=Plan(intent="continue")
    s=a.turn(reply,s)
    assert len(model.calls)==2
    assert_proposal(s,service,before)


def test_missing_name_does_not_drop_booking_fields(service):
    a,model=setup_booking(service)
    s=a.turn(BOOKING + "; DOB: 1988-04-20")
    assert (s["test"],s["requested_date"],s["selected_slot"])==("CBC","2026-11-12","13:00")
    assert s["awaiting_identity"] and not s.get("confirmation")
    model.result=Plan(intent="continue")
    s=a.turn(LABELLED,s)
    assert_proposal(s,service,2)


@pytest.mark.parametrize("duplicate,expected",[(False,"identity_not_found"),(True,"identity_not_unique")])
def test_zero_and_multiple_exact_matches_never_prepare_or_book(service,duplicate,expected):
    a,model=setup_booking(service)
    with service.repository.connection() as db:
        if duplicate:
            db.execute("INSERT INTO patients(id,name,date_of_birth) SELECT 'DUPLICATE',name,date_of_birth FROM patients WHERE id='SYN002'")
        else:
            db.execute("UPDATE patients SET name='Different Synthetic Record' WHERE id='SYN002'")
    before=len(service.repository.appointments())
    s=a.turn(BOOKING + "; " + LABELLED)
    assert [e["result"]["code"] for e in s["tool_results"]]==[expected]
    assert not s.get("patient") and not s.get("pending") and not s.get("confirmation")
    assert (s["test"],s["requested_date"],s["selected_slot"])==("CBC","2026-11-12","13:00")
    model.result=Plan(intent="confirm")
    s=a.turn("confirm",s)
    assert not s["tool_results"] and len(service.repository.appointments())==before


def test_explicit_birth_and_appointment_dates_are_separate_on_followup():
    name,dob=extract_identity(LABELLED+"; appointment date: 2026-11-12",awaiting=True)
    assert name=="Jordan Sample" and dob==date(1988,4,20)
