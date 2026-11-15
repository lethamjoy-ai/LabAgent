from tests.mock_router import OfflineRouter
import json
import httpx
import pytest
from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway
from app.integrations.labrag_client import HttpLabRAGClient
from tests.test_labrag import payload

BONE="What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"
PREP="How should a patient prepare for C-Peptide, Serum?"


@pytest.mark.parametrize("question",[
    PREP, "How should I prepare for C-Peptide, Serum?",
    "How is a patient prepared for this test?", "Should a patient fast for C-Peptide?",
    "Does the patient need fasting before this test?", "Can the patient eat before C-Peptide?",
    "What collection instructions apply to C-Peptide, Serum?",
    "How should the blood sample be collected?",
])
def test_preparation_verbs_route_to_laboratory_information(question):
    assert OfflineRouter.route(question)=="information"


def responses(question,count):
    if question==BONE:
        return {**payload("insufficient_evidence"),"answer":"The indexed passages do not establish an answer.",
            "citations":[],"matched_tests":[{"test_code":"BAP","test_name":"Bone Alkaline Phosphatase, Serum"}],"request_id":f"request-{count}"}
    return {**payload(),"answer":"Preparation instructions from a synthetic reference [1].",
        "matched_tests":[{"test_code":"CPEP","test_name":"C-Peptide, Serum"}],"request_id":f"request-{count}"}


def test_exact_screenshot_sequence_calls_api_for_preparation(service):
    sent=[]
    def handle(request):
        body=json.loads(request.content)
        sent.append(body)
        return httpx.Response(200,json=responses(body["question"],len(sent)))
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),
        HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handle)),now=service.now)
    s=a.turn(BONE)
    assert "insufficient evidence" in s["response"]
    s=a.turn(PREP,s)
    assert s["intent"]=="information" and not s["awaiting_identity"]
    assert s["tool_results"][0]["request"]["action"]=="information"
    assert sent==[{"question":BONE},{"question":PREP}]
    assert "Preparation instructions" in s["response"]
    assert "date of birth" not in s["response"].lower()
    assert s["history"][-1]["labrag_activity"]["request_id"]=="request-2"
    assert s["history"][-3]["labrag_activity"]["status"]=="insufficient_evidence"
    s=a.turn("I think you got it wrong",s)
    assert not s["tool_results"] and len(sent)==2
    assert "restate the laboratory question" in s["response"]
    assert not s["awaiting_identity"]
    s=a.turn(PREP,s)
    assert sent[-1]=={"question":PREP} and len(sent)==3
    assert "Preparation instructions" in s["response"] and not s["awaiting_identity"]


def test_preparation_recovers_from_pending_patient_lookup(service):
    sent=[]
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=responses(PREP,1))
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),
        HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handle)),now=service.now)
    s=a.turn("Find the patient")
    assert s["awaiting_identity"]
    s=a.turn(PREP,s)
    assert sent==[{"question":PREP}] and not s["awaiting_identity"]
    assert "Preparation instructions" in s["response"]


def test_patient_record_and_booking_intents_are_preserved():
    assert OfflineRouter.route("Find the patient")=="patient"
    assert OfflineRouter.route("Show patient appointments")=="retrieval"
    assert OfflineRouter.route("Schedule CBC tomorrow at 10 AM")=="booking"


def test_screenshot_sequence_and_request_details_in_ui(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    sent=[]
    def handle(request):
        body=json.loads(request.content)
        sent.append(body)
        return httpx.Response(200,json=responses(body["question"],len(sent)))
    ui=ui_for(service,monkeypatch)
    monkeypatch.setattr("app.bootstrap.build_labrag_client",lambda:HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handle)))
    sign_in(ui)
    for question in (BONE,PREP,"I think you got it wrong",PREP):
        ui.chat_input[0].set_value(question).run(timeout=30)
        assert not ui.exception
    assert sent==[{"question":BONE},{"question":PREP},{"question":PREP}]
    replies=[m["content"] for m in ui.session_state["conversation"]["history"] if m["role"]=="assistant"]
    assert all("date of birth" not in m.lower() for m in replies)
    assert sum("Preparation instructions" in m for m in replies)==2
    assert sum(bool(m.get("labrag_activity")) for m in ui.session_state["conversation"]["history"])==3
    assert not any(e.label=="LabRAG request details" for e in ui.main.expander)
    assert any(m.value=="**LabRAG request details**" for m in ui.sidebar.markdown)
    for message in ui.session_state["conversation"]["history"]:
        if "labrag_activity" in message:
            assert "question" not in message["labrag_activity"]
