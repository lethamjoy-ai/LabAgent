import json
import httpx
import pytest
from app.agents.planner import Plan, ModelPlanner
from tests.test_llm_intent_router import RecordingModel, assistant_for
from tests.test_labrag import payload
from app.integrations.labrag_client import HttpLabRAGClient

@pytest.mark.parametrize("message", ["yes", "cancel", "tomorrow", "I think you got it wrong", "Bone Alkaline Phosphatase, Serum", "hello", "Alex Demo 1990-01-15"])
def test_every_message_crosses_model_boundary(service,message):
    model=RecordingModel(Plan(intent="clarify",clarification="Which task?"))
    a=assistant_for(service,model)
    a.route=lambda *_:pytest.fail("Runtime keyword routing is forbidden")
    s=a.turn(message)
    assert len(model.calls)==1 and s["routing"]["call_count"]==1
    assert not s["tool_results"]
    assert "Alex Demo" not in str(model.calls) and "1990-01-15" not in str(model.calls)

@pytest.mark.parametrize("identity", ["Alex Demo 1990-01-15", "Taylor Example, June 10, 1995", "New Person, January 15, 1990", "name: New Person DOB January 15, 1990", "New Person 01/15/1990"])
def test_identity_reply_is_redacted_before_model(service,identity):
    model=RecordingModel(Plan(intent="continue"))
    a=assistant_for(service,model)
    s=a.turn(identity,{"context_session":__import__("app.staff.auth",fromlist=["token_hash"]).token_hash(service.test_access.session_token),"intent":"booking","awaiting_identity":True})
    assert len(model.calls)==1
    serialized=str(model.calls)
    for private in ("Alex Demo","Taylor Example","New Person","1990-01-15","June 10","January 15","01/15/1990"):
        assert private not in serialized


@pytest.mark.parametrize("error,category", [(TimeoutError(),"timeout"),(RuntimeError("private provider details"),"provider_failure")])
def test_provider_failures_are_safe_and_retryable(service,error,category):
    class Broken(RecordingModel):
        def invoke(self,messages):
            self.calls.append(messages)
            raise error
    model=Broken(None)
    s=assistant_for(service,model).turn("confirm")
    assert len(model.calls)==1 and not s["tool_results"]
    assert s["routing"]["failure_category"]==category
    assert "private provider details" not in str(s)
    assert len(service.repository.appointments())==2


def test_mixed_request_retains_lab_and_booking(service):
    question="What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"
    model=RecordingModel(Plan(intent="information",actions=[Plan(intent="information",question=question),Plan(intent="booking",test_id="CBC",day="2030-01-02",time="11:00",fresh_booking=True)]))
    sent=[]
    client=HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(lambda r:(sent.append(json.loads(r.content)) or httpx.Response(200,json=payload()))))
    s=assistant_for(service,model,client).turn(question+" And book CBC tomorrow at 11 AM.")
    assert len(model.calls)==1 and sent==[{"question":question}]
    assert "Sources:" in s["response"] and "patient's first name" in s["response"]
    assert s["test"]=="CBC" and s["requested_date"]=="2030-01-02"
    assert len(service.repository.appointments())==2


def test_mixed_request_defers_booking_across_lab_clarification(service):
    question="What specimen and volume are required?"
    model=RecordingModel(Plan(intent="information",actions=[Plan(intent="information",question=question),Plan(intent="booking",test_id="CBC",day="2030-01-15",time="11:00",fresh_booking=True)]))
    sent=[]
    def handler(r):
        sent.append(json.loads(r.content)["question"])
        return httpx.Response(200,json=payload("needs_clarification" if len(sent)==1 else "answered"))
    a=assistant_for(service,model,HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handler)))
    s=a.turn(question+" Also book CBC on January 15, 2030 at 11 AM.")
    assert s["queued_plans"] and s["intent"]=="information"
    model.result=Plan(intent="continue")
    s=a.turn("Bone Alkaline Phosphatase, Serum",s)
    assert len(model.calls)==2 and len(sent)==2
    assert "specimen and volume" in sent[1] and "Bone Alkaline Phosphatase, Serum" in sent[1]
    assert s["intent"]=="booking" and s["test"]=="CBC"
    assert "patient's first name" in s["response"] and not s["queued_plans"]
    assert len(service.repository.appointments())==2

@pytest.mark.parametrize("result",[{"intent":"delete_database"},{"intent":"booking","tool":"book"},{"intent":"booking","time":"25:99"},{"intent":"booking","actions":[{"intent":"booking"},{"intent":"unknown"}]}])
def test_invalid_entire_plan_never_executes(service,result):
    model=RecordingModel(result)
    s=assistant_for(service,model).turn("confirm")
    assert len(model.calls)==1 and not s["tool_results"]
    assert s["routing"]["failure_category"]=="invalid_output"
    assert len(service.repository.appointments())==2


def test_known_patient_data_blocked_from_labrag_without_selected_patient(service):
    model=RecordingModel(Plan(intent="information"))
    sent=[]
    client=HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(lambda r:(sent.append(r) or httpx.Response(200,json=payload()))))
    s=assistant_for(service,model,client).turn("What specimen for Alex Demo for BUN?")
    assert len(model.calls)==1 and "Alex Demo" not in str(model.calls)
    assert sent==[] and "without patient names" in s["response"]


def test_ui_rerender_does_not_repeat_router(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    ui=ui_for(service,monkeypatch)
    sign_in(ui)
    model=RecordingModel(Plan(intent="overview",day="2030-01-15"))
    ui.session_state["assistant"].planner=ModelPlanner(model)
    ui.chat_input[0].set_value("Show today's work").run(timeout=30)
    before=len(ui.session_state["conversation"]["history"])
    ui.run(timeout=30)
    ui.run(timeout=30)
    assert len(model.calls)==1
    assert len(ui.session_state["conversation"]["history"])==before


def test_unconfigured_router_has_no_keyword_fallback(service):
    a=assistant_for(service,RecordingModel(Plan(intent="information")))
    a.planner=None
    s=a.turn("Book CBC tomorrow at 11 AM")
    assert s["routing"]["failure_category"]=="not_configured"
    assert not s["tool_results"] and len(service.repository.appointments())==2


def test_booking_continuation_cannot_mistake_dob_for_appointment_date(service):
    model=RecordingModel(Plan(intent="booking",fresh_booking=True,test_id="CBC",day="2030-01-15",time="11:00"))
    a=assistant_for(service,model)
    s=a.turn("Schedule CBC on 2030-01-15 at 11:00 with Riley")
    model.result=Plan(intent="booking",fresh_booking=False)
    s=a.turn("Alex Demo 1990-01-15",s)
    assert len(model.calls)==2 and s["requested_date"]=="2030-01-15"
    assert s["confirmation"] and len(service.repository.appointments())==2
    assert "Alex Demo" not in str(model.calls) and "1990-01-15" not in str(model.calls)
