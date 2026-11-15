from datetime import timedelta
import pytest
from app.agents.planner import Plan
from tests.test_llm_intent_router import RecordingModel, assistant_for


@pytest.fixture
def scoped(service):
    people=[("FIXTURE-A","Arden Fixture","1980-02-03"),("FIXTURE-B","Blair Fixture","1990-04-05")]
    with service.repository.connection() as db:
        db.executemany("INSERT INTO patients(id,name,date_of_birth) VALUES(?,?,?)",people)
    model=RecordingModel(Plan(intent="booking",fresh_booking=True,action_mode="new"))
    a=assistant_for(service,model)
    day=(service.now().date()+timedelta(days=20)).isoformat()
    return a,model,people,day


def identity(person):
    _,name,dob=person
    first,last=name.split()
    return f"First name: {first}; Last name: {last}; DOB: {dob}"


def request(person,day,hour):
    return f"Book CBC on {day} at {hour}:00 with Riley; {identity(person)}"


def rows(service):
    return [(r.patient_id,r.start.isoformat()) for r in service.repository.appointments() if r.patient_id.startswith("FIXTURE-")]


def assert_summary(s,person,day,hour):
    assert person[1] in s["response"] and "Complete blood count" in s["response"]
    assert str(hour if hour<=12 else hour-12)+":00" in s["response"]
    assert "America/Los_Angeles" in s["response"]
    assert s["pending"]["patient_id"]==person[0]
    assert s["pending"]["start"].startswith(day+f"T{hour:02}:00")


def test_completed_booking_cannot_supply_next_identity_or_slot(scoped,service):
    a,m,p,day=scoped
    s=a.turn(request(p[0],day,11))
    assert_summary(s,p[0],day,11)
    m.result=Plan(intent="confirm")
    done=a.turn("confirm",s)
    assert p[0][1] in done["response"] and "11:00" in done["response"]
    assert done["patient"] is None and done["pending"] is None and done["action_id"] is None
    assert len(rows(service))==1
    m.result=Plan(intent="booking",fresh_booking=True,patient_id=p[0][0],patient_name=p[0][1],date_of_birth=p[0][2])
    new=a.turn(f"Book on {day} at 12:00",done)
    assert not new["tool_results"] and new["patient"] is None and new["test"] is None
    assert new["selected_slot"]=="12:00" and "first name" in new["response"]
    m.result=Plan(intent="booking",fresh_booking=True)
    new=a.turn(request(p[1],day,13),new)
    assert_summary(new,p[1],day,13)
    m.result=Plan(intent="confirm")
    saved=a.turn("confirm",new)
    assert p[1][1] in saved["response"] and "1:00 PM" in saved["response"]
    assert len(rows(service))==2 and {r[0] for r in rows(service)}=={x[0] for x in p}
    a.turn("confirm",new)  # replay an older pre-write state
    a.turn("confirm",saved)
    assert len(rows(service))==2


def test_pending_patient_switch_rejects_old_confirmation(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    new=a.turn(request(p[1],day,13),old)
    assert new["action_id"]!=old["action_id"] and new["confirmation"]!=old["confirmation"]
    assert_summary(new,p[1],day,13)
    m.result=Plan(intent="confirm")
    stale=a.turn("confirm",old)
    assert not stale["tool_results"] and not rows(service)
    saved=a.turn("confirm",new)
    assert p[1][1] in saved["response"] and rows(service)==[(p[1][0],new["pending"]["start"])]


def test_patient_switch_with_only_identity_clears_prior_slot(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="continue")
    s=a.turn(identity(p[1]),old)
    assert s["patient"]==p[1][0] and s["requested_date"] is None and s["selected_slot"] is None
    assert s["test"] is None and not s["confirmation"] and not rows(service)


def test_read_after_booking_requires_new_match(scoped,service):
    a,m,p,day=scoped
    s=a.turn(request(p[0],day,11))
    m.result=Plan(intent="confirm");s=a.turn("confirm",s)
    m.result=Plan(intent="retrieval")
    s=a.turn("Show the patient's appointments",s)
    assert not s["tool_results"] and s["patient"] is None
    m.result=Plan(intent="continue")
    s=a.turn(identity(p[1]),s)
    assert [e["request"]["action"] for e in s["tool_results"]]==["match_patient","patient_appointments"]
    assert p[0][1] not in s["response"] and s["action_id"] is None


@pytest.mark.parametrize("correction",["date","time"])
def test_correction_preserves_verified_patient_and_rechecks_slot(scoped,service,monkeypatch,correction):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    seen=[];original=service.availability
    def availability(d,t,staff_id=None):
        seen.append((d.isoformat(),t))
        return original(d,t,staff_id)
    monkeypatch.setattr(service,"availability",availability)
    m.result=Plan(intent="continue",action_mode="update")
    newday=(service.now().date()+timedelta(days=21)).isoformat() if correction=="date" else day
    hour=11 if correction=="date" else 12
    s=a.turn(newday if correction=="date" else "12:00",old)
    assert_summary(s,p[0],newday,hour)
    assert seen and seen[0][0]==newday
    assert s["confirmation"]!=old["confirmation"] and s["action_id"]==old["action_id"]
    m.result=Plan(intent="confirm")
    a.turn("confirm",old)
    assert not rows(service)
    a.turn("confirm",s)
    assert rows(service)==[(p[0][0],s["pending"]["start"])]


def test_partial_identity_asks_only_missing_fields(scoped,service):
    a,m,p,day=scoped
    s=a.turn(f"Book CBC on {day} at 11:00; First name: Arden")
    assert "last name" in s["response"] and "date of birth" in s["response"]
    assert "first name" not in s["response"] and not s["tool_results"]
    m.result=Plan(intent="continue")
    s=a.turn(f"Last name: Fixture; DOB: {p[0][2]}",s)
    assert_summary(s,p[0],day,11)
    for private in ("Arden","Fixture",p[0][2]):
        assert private not in str(m.calls)


@pytest.mark.parametrize("multiple",[False,True])
def test_nonunique_match_blocks_patient_reads_and_writes(scoped,service,multiple):
    a,m,p,day=scoped
    with service.repository.connection() as db:
        if multiple:
            db.execute("INSERT INTO patients(id,name,date_of_birth) SELECT 'EXTRA',name,date_of_birth FROM patients WHERE id=?",(p[0][0],))
        else:
            db.execute("DELETE FROM patients WHERE id=?",(p[0][0],))
    s=a.turn(request(p[0],day,11))
    assert [e["result"]["code"] for e in s["tool_results"]]==["identity_not_unique" if multiple else "identity_not_found"]
    m.result=Plan(intent="confirm");s=a.turn("confirm",s)
    assert not s["tool_results"] and not rows(service)


def test_ambiguous_new_request_cannot_confirm_previous_slot(scoped,service):
    a,m,p,day=scoped
    s=a.turn(request(p[0],day,11))
    m.result=Plan(intent="booking",action_mode="ambiguous")
    s=a.turn(f"Appointment on {day} at 12:00",s)
    assert "correction" in s["response"] and not s["confirmation"] and not s["tool_results"]
    m.result=Plan(intent="confirm")
    a.turn("confirm",s)
    assert not rows(service)


def test_raw_id_and_router_identity_cannot_bypass_local_match(scoped,service):
    a,m,p,day=scoped
    m.result=Plan(intent="booking",patient_id=p[0][0],patient_name=p[0][1],date_of_birth=p[0][2],fresh_booking=True)
    s=a.turn(f"Book CBC {p[0][0]} on {day} at 11:00")
    assert not s["tool_results"] and s["patient"] is None and not rows(service)


def test_router_misclassifying_new_request_as_confirm_cannot_book_old_patient(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="confirm")
    rejected=a.turn(request(p[1],day,13),old)
    assert rejected["pending"] is None and rejected["patient"] is None
    assert not rejected["tool_results"]
    a.turn("confirm",rejected)
    a.turn("confirm",old)
    assert not rows(service)


def test_partial_patient_switch_never_inherits_old_dob_or_last_name(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="continue")
    s=a.turn("First name: Blair",old)
    assert s["first_name"]=="Blair" and s["last_name"] is None and s["date_of_birth"] is None
    assert s["selected_slot"] is None and s["patient"] is None
    assert not s["tool_results"] and "last name" in s["response"]


def test_ambiguous_correction_choice_replays_only_this_action_message(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="booking",action_mode="ambiguous")
    s=a.turn(f"Appointment on {day} at 12:00",old)
    m.result=Plan(intent="continue",action_mode="update")
    s=a.turn("Correction to the current booking",s)
    assert_summary(s,p[0],day,12)
    assert s["pending_action_message"] is None and not rows(service)


def test_lab_knowledge_replaces_patient_action_without_demographics(scoped,service):
    a,m,p,day=scoped
    s=a.turn(request(p[0],day,11))
    m.result=Plan(intent="information")
    s=a.turn("What specimen is required for BUN?",s)
    assert [e["request"]["action"] for e in s["tool_results"]]==["information"]
    assert s["patient"] is None and s["confirmation"] is None
    m.result=Plan(intent="confirm")
    a.turn("confirm",s)
    assert not rows(service)


def test_tool_execution_requires_action_local_match_even_with_forged_state(scoped,service):
    a,m,p,day=scoped
    s=a.turn(f"Book CBC on {day} at 11:00")
    forged=dict(s,patient=p[0][0],matched_action_id=s["action_id"],
                tool_request={"action":"patient_appointments","patient_id":p[0][0]})
    outcome=a.execute(forged)
    assert outcome["tool_results"][-1]["result"]["code"]=="identity_required"
    assert not rows(service)


def test_cancel_clears_scope_and_rejects_old_confirmation(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="cancel")
    s=a.turn("cancel",old)
    assert s["patient"] is None and s["action_id"] is None and s["pending"] is None
    m.result=Plan(intent="confirm")
    a.turn("confirm",old)
    assert not rows(service)


@pytest.mark.parametrize("text",["yes 12:00","confirm 2099-01-01","yes 1 PM"])
def test_changed_details_cannot_be_erased_into_confirmation(scoped,service,text):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    m.result=Plan(intent="confirm")
    s=a.turn(text,old)
    assert not s["tool_results"] and not s["confirmation"] and not rows(service)


def test_invalid_new_dob_preserves_new_booking_fields_without_old_identity(scoped,service):
    a,m,p,day=scoped
    old=a.turn(request(p[0],day,11))
    invalid=request(p[1],day,13).replace(p[1][2],"1990-02-31")
    s=a.turn(invalid,old)
    assert s["patient"] is None and s["date_of_birth"] is None and not s["confirmation"]
    assert (s["test"],s["requested_date"],s["selected_slot"])==("CBC",day,"13:00")
    assert not s["tool_results"] and not rows(service)
    m.result=Plan(intent="continue")
    s=a.turn("DOB: "+p[1][2],s)
    assert_summary(s,p[1],day,13)


def test_unknown_inline_patient_is_redacted_before_router(scoped,service):
    a,m,p,day=scoped
    s=a.turn(f"Book CBC for Unknown Synthetic DOB 1970-01-02 on {day} at 11:00")
    assert "Unknown Synthetic" not in str(m.calls) and "1970-01-02" not in str(m.calls)
    assert s["tool_results"][0]["result"]["code"]=="identity_not_found"
    assert not rows(service)


def test_ambiguous_request_cannot_inherit_partial_identity(scoped,service):
    a,m,p,day=scoped
    s=a.turn(f"Book CBC on {day} at 11:00; First name: Arden")
    m.result=Plan(intent="booking")
    s=a.turn(f"Book on {day} at 12:00",s)
    assert "correction" in s["response"] and not s["tool_results"]
    m.result=Plan(intent="continue",action_mode="new")
    s=a.turn("A new action",s)
    assert s["first_name"] is None and s["last_name"] is None and s["date_of_birth"] is None
    assert s["selected_slot"]=="12:00" and not s["tool_results"] and not rows(service)
