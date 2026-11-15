import json
import httpx
import pytest
from openai import OpenAI
from app.agents.planner import OpenAIModel, ModelPlanner, Plan, openai_planner
from tests.test_llm_intent_router import RecordingModel, assistant_for


def response_body(content=None):
    return {"id":"resp_offline", "object":"response", "created_at":0,
        "model":"gpt-4.1-mini", "status":"completed",
        "output":[{"id":"msg_offline","type":"message","status":"completed","role":"assistant",
                   "content":content or [{"type":"output_text","text":Plan(intent="overview",day="2030-01-15").model_dump_json(),"annotations":[]}]}]}


def test_openai_sdk_structured_request_and_privacy(service):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        assert str(request.url)=="https://api.openai.com/v1/responses"
        return httpx.Response(200,json=response_body())
    client=OpenAI(api_key="offline-placeholder",max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    a=assistant_for(service,RecordingModel(Plan(intent="overview")))
    a.planner=ModelPlanner(OpenAIModel(client,"gpt-4.1-mini"))
    s=a.turn("Show Alex Demo's schedule on January 15, 2030")
    assert len(sent)==1 and sent[0]["store"] is False
    assert sent[0]["text"]["format"]["type"]=="json_schema"
    assert sent[0]["text"]["format"]["strict"] is True
    schema=sent[0]["text"]["format"]["schema"]
    assert '"default":' not in json.dumps(schema)
    assert "Alex Demo" not in json.dumps(sent)
    assert s["tool_results"][0]["request"]["action"]=="overview"


@pytest.mark.parametrize("failure",["timeout","http_error","refusal"])
def test_openai_failure_does_not_retry_or_execute(service,failure):
    sent=[]
    def handler(request):
        sent.append(request)
        if failure=="timeout":
            raise httpx.ReadTimeout("offline timeout",request=request)
        if failure=="http_error":
            return httpx.Response(503,json={"error":{"message":"offline unavailable","type":"server_error"}})
        return httpx.Response(200,json=response_body([{"type":"refusal","refusal":"offline refusal"}]))
    client=OpenAI(api_key="offline-placeholder",max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    a=assistant_for(service,RecordingModel(Plan(intent="overview")))
    a.planner=ModelPlanner(OpenAIModel(client,"gpt-4.1-mini"))
    s=a.turn("confirm")
    assert len(sent)==1 and not s["tool_results"]
    assert s["routing"]["status"]=="failed"
    assert len(service.repository.appointments())==2


def test_openai_factory_has_bounded_calls():
    planner=openai_planner("gpt-4.1-mini","offline-placeholder")
    assert planner.structured.client.max_retries==0
    assert planner.structured.client.timeout==15
    planner.structured.client.close()


def test_sidebar_switch_preserves_history_without_resubmitting(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    groq=RecordingModel(Plan(intent="overview",day="2030-01-15"))
    openai=RecordingModel(Plan(intent="overview",day="2030-01-15"))
    monkeypatch.setenv("OPENAI_API_KEY","offline-placeholder")
    monkeypatch.setattr("app.agents.planner.groq_planner",lambda *args:ModelPlanner(groq))
    monkeypatch.setattr("app.agents.planner.openai_planner",lambda *args:ModelPlanner(openai))
    ui=ui_for(service,monkeypatch)
    sign_in(ui)
    ui.chat_input[0].set_value("Show my schedule").run(timeout=30)
    assert len(groq.calls)==1
    history=ui.session_state["conversation"]["history"]
    ui.selectbox[0].set_value("openai").run(timeout=30)
    assert not ui.exception and ui.session_state["assistant"].router_provider=="openai"
    assert ui.session_state["conversation"]["history"]==history
    assert len(groq.calls)==1 and len(openai.calls)==0
    ui.chat_input[0].set_value("Show my daily overview").run(timeout=30)
    assert len(openai.calls)==1
    ui.selectbox[0].set_value("groq").run(timeout=30)
    assert len(groq.calls)==1 and len(openai.calls)==1


def test_missing_selected_provider_key_has_no_fallback(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    monkeypatch.setenv("OPENAI_API_KEY","")
    ui=ui_for(service,monkeypatch)
    sign_in(ui)
    ui.selectbox[0].set_value("openai").run(timeout=30)
    assert ui.error and not ui.chat_input
    ui.selectbox[0].set_value("groq").run(timeout=30)
    assert ui.chat_input and not ui.exception
