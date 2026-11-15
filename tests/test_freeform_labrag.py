import json
import httpx
import pytest
from app.agents.lab_assistant import LabAssistant
from app.agents.planner import ModelPlanner, Plan
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.models import TESTS
from app.integrations.labrag_client import HttpLabRAGClient
from tests.test_labrag import payload

QUESTION = "What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"
TEST_NAME = "Bone Alkaline Phosphatase, Serum"


def make_assistant(service,handler):
    return LabAssistant(DirectAppointmentGateway(service,service.test_access),
        HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handler)),now=service.now)


@pytest.mark.parametrize("question",[QUESTION,
    "What preparation is required for Anti-Mullerian Hormone, Serum?",
    "What specimen and volume are required for CSF Oligoclonal Bands?"])
def test_arbitrary_test_names_reach_http_unchanged(service,question):
    sent=[]
    def handler(request):
        assert request.url.path == "/api/v1/query"
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=payload())
    s=make_assistant(service,handler).turn(question)
    assert sent == [{"question":question}]
    assert s["tool_results"][0]["request"]["test_id"] is None
    assert s["tool_results"][0]["result"]["status"] == "answered"
    assert "Sources:" in s["response"]
    assert "Which laboratory test?" not in s["response"]


def test_clarification_reply_keeps_full_original_request(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content)["question"])
        response=payload("needs_clarification" if len(sent)==1 else "answered")
        if len(sent)==1: response["answer"]="Which exact catalog test name do you mean?"
        return httpx.Response(200,json=response)
    a=make_assistant(service,handler)
    original="What specimen and volume are required?"
    s=a.turn(original)
    assert sent == [original] and "Which exact catalog" in s["response"]
    s=a.turn(TEST_NAME,s)
    assert len(sent)==2 and sent[1].startswith(original)
    assert TEST_NAME in sent[1] and "specimen and volume" in sent[1]
    assert "Sources:" in s["response"] and s["labrag_original_question"] is None


def test_api_clarification_loop_stops_without_duplicate_queries(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content)["question"])
        response=payload("needs_clarification")
        response["answer"]="Which laboratory test?"
        return httpx.Response(200,json=response)
    a=make_assistant(service,handler)
    s=a.turn(QUESTION)
    assert "Which laboratory test?" in s["response"]  # genuine API response
    s=a.turn(TEST_NAME,s)
    assert len(sent)==2
    assert "still could not resolve" in s["response"] and "catalog test code" in s["response"]
    assert "Which laboratory test?" not in s["response"]
    assert "Printed catalog page: 42" in s["response"]
    assert s["tool_results"][0]["result"]["answer"] == "Which laboratory test?"
    s=a.turn(TEST_NAME,s)
    assert len(sent)==2 and not s["tool_results"]
    assert "already been sent" in s["response"]


def test_insufficient_evidence_preserves_explanation_and_citations(service):
    response={**payload("insufficient_evidence"),"answer":"The indexed references do not establish specimen volume for this test."}
    s=make_assistant(service,lambda r:httpx.Response(200,json=response)).turn(QUESTION)
    assert "insufficient evidence" in s["response"] and response["answer"] in s["response"]
    assert s["tool_results"][0]["result"]["citations"] == response["citations"]
    assert "Which laboratory test?" not in s["response"]


def test_knowledge_path_ignores_previous_appointment_test_and_patient_context(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=payload())
    a=make_assistant(service,handler)
    s=a.turn("Schedule CBC 2030-01-15 11:00 with Riley")
    s=a.turn("Alex Demo 1990-01-15",s)
    s=a.turn(QUESTION,s)
    assert sent==[{"question":QUESTION}]
    assert "Alex Demo" not in str(sent) and "1990-01-15" not in str(sent) and "CBC" not in str(sent)


def test_bun_still_works_and_appointment_catalog_is_unchanged(service):
    sent=[]
    a=make_assistant(service,lambda r:(sent.append(json.loads(r.content)) or httpx.Response(200,json=payload())))
    s=a.turn("What specimen is required for BUN?")
    assert sent==[{"question":"What specimen is required for BUN?"}]
    assert "Sources:" in s["response"] and set(TESTS)=={"CBC","GLU","A1C"}
    s=a.turn("Schedule Bone Alkaline Phosphatase, Serum tomorrow at 11:00",s)
    s=a.turn("Alex Demo 1990-01-15",s)
    assert not s.get("confirmation") and len(sent)==1
    assert "laboratory test" in s["response"]


def test_fragment_with_information_keywords_is_a_clarification(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content)["question"])
        return httpx.Response(200,json=payload("needs_clarification" if len(sent)==1 else "answered"))
    a=make_assistant(service,handler)
    s=a.turn(QUESTION)
    s=a.turn("Specimen: Serum",s)
    assert sent[1].startswith(QUESTION) and "Specimen: Serum" in sent[1]


def test_control_turn_is_not_a_chargeable_test_name_reply(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=payload("needs_clarification"))
    a=make_assistant(service,handler)
    s=a.turn(QUESTION)
    s=a.turn("Yes, confirm.",s)
    assert len(sent)==1 and not s["tool_results"]
    assert "No booking" in s["response"]


def test_model_information_route_does_not_replace_original_question(service):
    question="Explain Bone Alkaline Phosphatase, Serum collection requirements."
    class FakeModel:
        def with_structured_output(self,schema): return self
        def invoke(self,messages): return Plan(intent="information",test_id=TEST_NAME)
    sent=[]
    a=make_assistant(service,lambda r:(sent.append(json.loads(r.content)) or httpx.Response(200,json=payload())))
    a.planner=ModelPlanner(FakeModel())
    s=a.turn(question)
    assert sent==[{"question":question}] and "Sources:" in s["response"]


def test_ui_unknown_test_answer_and_sources(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={**payload(),"answer":"Bone-specific reference answer [1]."})
    ui=ui_for(service,monkeypatch)
    monkeypatch.setattr("app.bootstrap.build_labrag_client",lambda:HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handler)))
    sign_in(ui)
    ui.chat_input[0].set_value(QUESTION).run(timeout=30)
    assert not ui.exception and sent==[{"question":QUESTION}]
    shown="\n".join(t.value for t in ui.text)
    assert "Bone-specific reference answer [1]." in shown
    message=ui.session_state["conversation"]["history"][-1]
    assert message["labrag_citations"][0]["printed_catalog_page"]=="42"
    assert any(e.label=="View sources" and not e.proto.expanded for e in ui.main.expander)
