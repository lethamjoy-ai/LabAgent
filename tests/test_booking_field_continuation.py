from datetime import timedelta
import json
import pytest
from app.agents.planner import Plan
from tests.test_llm_intent_router import RecordingModel, assistant_for


@pytest.fixture
def booking(service):
    people=[("PERSON-A","Quinn Fixture","1976-03-04"),("PERSON-B","Skyler Fixture","1986-05-06")]
    with service.repository.connection() as db:
        db.execute("DELETE FROM appointments")
        db.execute("DELETE FROM confirmations")
        db.execute("DELETE FROM patients")
        db.executemany("INSERT INTO patients(id,name,date_of_birth) VALUES(?,?,?)",people)
    model=RecordingModel(Plan(intent="booking",fresh_booking=True,action_mode="new"))
    assistant=assistant_for(service,model)
    day=(service.now().date()+timedelta(days=24)).isoformat()
    return assistant,model,people,day


def full(person,day,label="Date of birth",time="11:00 AM"):
    _,name,dob=person
    first,last=name.split()
    return f"Book CBC; First name: {first}; Last name: {last}; {label}: {dob}; Appointment date: {day}; Appointment time: {time}; with Riley"


def assert_confirmation(s,person,day,time):
    assert s["confirmation"] and s["pending"]["patient_id"]==person[0]
    assert s["pending"]["start"].startswith(day+"T"+time)
    assert s["pending"]["test_id"]=="CBC"
    assert person[1] in s["response"] and "America/Los_Angeles" in s["response"]
    assert not s["awaiting_booking_fields"]


@pytest.mark.parametrize("label",["Date of birth","Birth date","Date of birth (DOB)","Date-of-birth","Date of birth (YYYY-MM-DD)","DOB -"])
def test_complete_labels_do_not_mix_birth_and_appointment_dates(booking,service,label):
    a,m,p,day=booking
    s=a.turn(full(p[0],day,label))
    assert_confirmation(s,p[0],day,"11:00")
    assert len(m.calls)==1 and not service.repository.appointments()
    body=json.loads(m.calls[0][-1][1])
    assert all(body["context"]["current_input_fields"].values())
    for private in (p[0][1],p[0][2],"Quinn","Fixture"):
        assert private not in str(m.calls)


@pytest.mark.parametrize("router_intent",["conversation","clarify","continue"])
def test_date_clarification_and_labeled_combined_reply_stay_in_booking(booking,service,router_intent):
    a,m,p,day=booking
    other=(service.now().date()+timedelta(days=25)).isoformat()
    s=a.turn(full(p[0],day+" or "+other))
    action=s["action_id"]
    assert "Which date" in s["response"] and s["awaiting_booking_fields"]==["date"]
    assert s["first_name"] and s["date_of_birth"]==p[0][2] and s["selected_slot"]=="11:00"
    m.result=Plan(intent=router_intent)
    s=a.turn(f"Appointment date: {day}; Appointment time: 2:00PM",s)
    assert s["action_id"]==action and len(m.calls)==2
    assert_confirmation(s,p[0],day,"14:00")
    assert not service.repository.appointments()


@pytest.mark.parametrize("reply",["2:00PM","Appointment time: 2:00 PM"])
def test_missing_time_accepts_time_only_even_when_model_says_conversation(booking,service,reply):
    a,m,p,day=booking
    message=full(p[0],day).replace("Appointment time: 11:00 AM; ","")
    s=a.turn(message)
    assert s["awaiting_booking_fields"]==["time"] and s["patient"]==p[0][0]
    m.result=Plan(intent="conversation")
    s=a.turn(reply,s)
    assert_confirmation(s,p[0],day,"14:00")
    assert len(m.calls)==2 and not service.repository.appointments()


def test_bare_date_reply_is_not_reinterpreted_as_dob(booking,service):
    a,m,p,day=booking
    message=full(p[0],day).replace(f"Appointment date: {day}; ","")
    s=a.turn(message)
    assert s["awaiting_booking_fields"]==["date"]
    m.result=Plan(intent="conversation")
    s=a.turn(day,s)
    assert s["date_of_birth"]==p[0][2]
    assert_confirmation(s,p[0],day,"11:00")


def test_conflicting_time_keeps_action_then_accepts_one_selection(booking,service):
    a,m,p,day=booking
    old=a.turn(full(p[0],day))
    m.result=Plan(intent="continue",action_mode="update")
    s=a.turn("Appointment time: 11:00 AM or 2:00PM",old)
    assert "Which time" in s["response"] and s["awaiting_booking_fields"]==["time"]
    assert s["selected_slot"] is None and s["pending"] is None and s["confirmation"] is None
    m.result=Plan(intent="conversation")
    s=a.turn("2:00PM",s)
    assert_confirmation(s,p[0],day,"14:00")
    m.result=Plan(intent="confirm")
    a.turn("confirm",old)
    assert not service.repository.appointments()
    saved=a.turn("confirm",s)
    records=service.repository.appointments()
    assert len(records)==1 and records[0].patient_id==p[0][0]
    assert records[0].start.isoformat()==s["pending"]["start"]
    assert p[0][1] in saved["response"] and "2:00 PM" in saved["response"]
    a.turn("confirm",saved)
    assert len(service.repository.appointments())==1


def test_unavailable_corrected_time_offers_current_date_alternatives(booking,service):
    a,m,p,day=booking
    service.staff.replace_hours("STAFF1",list(range(7)),"09:00","14:00")
    s=a.turn(full(p[0],day))
    m.result=Plan(intent="continue",action_mode="update")
    s=a.turn("Appointment time: 2:00PM",s)
    assert s["tool_results"][-1]["result"]["code"]=="unavailable"
    assert s["awaiting_booking_fields"]==["time"] and s["confirmation"] is None
    alternatives=s["tool_results"][-1]["result"]["slots"]
    assert alternatives and all(slot["start"].startswith(day) for slot in alternatives)
    assert "Available alternatives" in s["response"]
    m.result=Plan(intent="conversation")
    s=a.turn("1:30PM",s)
    assert_confirmation(s,p[0],day,"13:30")
    assert not service.repository.appointments()


def test_independent_schedule_and_new_booking_do_not_consume_field_reply_state(booking,service):
    a,m,p,day=booking
    s=a.turn(full(p[0],day+" or "+(service.now().date()+timedelta(days=25)).isoformat()))
    m.result=Plan(intent="overview")
    s=a.turn("Show my schedule today",s)
    assert [e["request"]["action"] for e in s["tool_results"]]==["overview"]
    assert s["patient"] is None and not s["awaiting_booking_fields"]
    m.result=Plan(intent="booking",fresh_booking=True)
    s=a.turn(f"Book CBC on {day} at 2:00PM",s)
    assert not s["tool_results"] and s["patient"] is None and "first name" in s["response"]
    assert s["requested_date"]==day and s["selected_slot"]=="14:00"


def test_invalid_time_reasks_only_time_without_losing_booking(booking,service):
    a,m,p,day=booking
    s=a.turn(full(p[0],day).replace("Appointment time: 11:00 AM; ",""))
    m.result=Plan(intent="conversation")
    s=a.turn("25:00",s)
    assert "valid appointment time" in s["response"] and s["patient"]==p[0][0]
    assert s["requested_date"]==day and s["awaiting_booking_fields"]==["time"]
    s=a.turn("2:00PM",s)
    assert_confirmation(s,p[0],day,"14:00")


def test_streamlit_chat_field_reply_and_confirmation_are_not_replayed(booking,service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    _,m,p,day=booking
    def build(access=None,provider=None):
        a=assistant_for(service,m)
        a.tools.gateway.access=access
        a.router_provider=provider or "groq"
        return a
    monkeypatch.setattr("app.bootstrap.create_assistant",build)
    ui=ui_for(service,monkeypatch)
    sign_in(ui)
    ui.chat_input[0].set_value(full(p[0],day,"Date of birth (DOB)")).run(timeout=30)
    assert not ui.exception and ui.session_state["conversation"]["confirmation"]
    m.result=Plan(intent="continue",action_mode="update")
    ui.chat_input[0].set_value("Appointment time: 11AM or 2PM").run(timeout=30)
    m.result=Plan(intent="conversation")
    ui.chat_input[0].set_value("2:00PM").run(timeout=30)
    assert_confirmation(ui.session_state["conversation"],p[0],day,"14:00")
    assert not service.repository.appointments() and len(m.calls)==3
    m.result=Plan(intent="confirm")
    ui.chat_input[0].set_value("confirm").run(timeout=30)
    saved=ui.session_state["conversation"]["response"]
    assert p[0][1] in saved and "2:00 PM" in saved and len(service.repository.appointments())==1
    ui.run(timeout=30)
    assert len(m.calls)==4
    ui.chat_input[0].set_value("confirm").run(timeout=30)
    assert len(service.repository.appointments())==1 and not ui.exception
