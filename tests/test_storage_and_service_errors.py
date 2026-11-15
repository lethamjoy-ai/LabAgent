from tests.mock_router import OfflineRouter
import json
import httpx
import pytest
from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway
from app.integrations.labrag_client import HttpLabRAGClient,LabRAGError
from tests.test_labrag import payload

PROCESSING="How should CPR be processed and stored?"
PREPARATION="How should a patient prepare for C-Peptide, Serum?"
REQUEST_ID="1a7a628e-81bc-4726-8ed5-1e5699216bd8"


@pytest.mark.parametrize("question",[
    PROCESSING,"How should CRP be processed and stored?",
    "How long can CRP be refrigerated?","Should this sample be frozen?",
    "What temperature is suitable for an unfamiliar assay?",
    "Does the sample need centrifugation?","When can this sample be discarded?",
])
def test_reference_questions_do_not_require_an_enumerated_verb(question):
    assert OfflineRouter.route(question)=="information"


@pytest.mark.parametrize("question,intent",[
    ("Can I book a CBC appointment tomorrow?","booking"),
    ("What appointments do I have tomorrow?","overview"),
    ("What is the patient's date of birth?","patient"),
    ("Find the patient record","patient"),
    ("What CBC slots are available tomorrow?","availability"),
    ("Could you finalize it",None),
    ("Can you assist me please",None),
    ("Can you help me?",None),
])
def test_explicit_appointment_and_record_routes_take_priority(question,intent):
    assert OfflineRouter.route(question)==intent


def test_processing_is_sent_unchanged_and_503_is_not_an_answer(service):
    sent=[]
    def handle(r):
        sent.append(json.loads(r.content))
        if len(sent)==1:
            return httpx.Response(200,json={**payload("needs_clarification"),"answer":"Please clarify the test abbreviation CPR."})
        return httpx.Response(503,json={"error":"provider_rate_limited","message":"private provider text must not be echoed","request_id":REQUEST_ID})
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handle)))
    s=a.turn(PROCESSING)
    assert sent==[{"question":PROCESSING}]  # CPR is not silently rewritten to CRP
    assert "test abbreviation CPR" in s["response"]
    s=a.turn(PREPARATION,s)
    assert sent==[{"question":PROCESSING},{"question":PREPARATION}]
    assert s["tool_results"][0]["result"]["code"]=="http_503"
    assert "rate-limited" in s["response"] and "No laboratory answer was returned" in s["response"]
    meta=s["history"][-1]["labrag_activity"]
    assert meta["request_id"]==REQUEST_ID and meta["api_error"]=="provider_rate_limited"
    assert "private provider" not in str(s["tool_results"])
    assert len(sent)==2  # no automatic retry


@pytest.mark.parametrize("tag",["service_unavailable","provider_rate_limited","upstream_timeout"])
def test_known_api_error_tags_and_request_ids_are_preserved(tag):
    client=HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(lambda r:httpx.Response(503,json={"error":tag,"request_id":REQUEST_ID})))
    with pytest.raises(LabRAGError) as exc:
        client.query(None,PREPARATION)
    assert exc.value.request_id==REQUEST_ID and exc.value.api_error==tag


def test_error_body_is_not_echoed_and_header_id_is_used_safely():
    client=HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(lambda r:httpx.Response(503,
        json={"error":"secret provider message","request_id":"patient name","detail":"private"},headers={"X-Request-ID":REQUEST_ID})))
    with pytest.raises(LabRAGError) as exc:
        client.query(None,PREPARATION)
    assert exc.value.request_id==REQUEST_ID and exc.value.api_error is None
    assert "private" not in str(exc.value) and "secret" not in str(exc.value)


def test_non_object_error_body_is_safe():
    client=HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(lambda r:httpx.Response(503,json=["private"])))
    with pytest.raises(LabRAGError) as exc:
        client.query(None,PREPARATION)
    assert exc.value.code=="http_503" and exc.value.request_id is None


def test_exact_screenshot_in_ui_with_mocked_http(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    sent=[]
    def handle(r):
        sent.append(json.loads(r.content))
        if len(sent)==1:
            return httpx.Response(200,json={**payload(),"answer":"Synthetic processing/storage result [1]."})
        return httpx.Response(503,json={"error":"service_unavailable","request_id":REQUEST_ID})
    ui=ui_for(service,monkeypatch)
    monkeypatch.setattr("app.bootstrap.build_labrag_client",lambda:HttpLabRAGClient("http://example.test",transport=httpx.MockTransport(handle)))
    sign_in(ui)
    for question in (PROCESSING,PREPARATION):
        ui.chat_input[0].set_value(question).run(timeout=30)
        assert not ui.exception
    assert sent==[{"question":PROCESSING},{"question":PREPARATION}]
    history=ui.session_state["conversation"]["history"]
    assert "Synthetic processing/storage result" in history[1]["content"]
    assert "No laboratory answer was returned" in history[-1]["content"]
    assert history[-1]["labrag_activity"]["request_id"]==REQUEST_ID
    assert history[-1]["labrag_activity"]["api_error"]=="service_unavailable"
    assert sum(bool(m.get("labrag_activity")) for m in history)==2
    assert not any(e.label=="LabRAG request details" for e in ui.main.expander)
    assert any(m.value=="**LabRAG request details**" for m in ui.sidebar.markdown)
