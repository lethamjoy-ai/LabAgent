from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import httpx
import pytest
from app.integrations.live_voice import signed_conversation_url,valid_start_event


@pytest.mark.parametrize('timeout,confirmed_type,valid',[(20,'boolean',False),(120,'string',False),(120,'boolean',True)])
def test_dashboard_timeout_and_confirmation_contract(monkeypatch,timeout,confirmed_type,valid):
    from app.integrations.live_voice import require_readonly_agent,LiveSetupError
    monkeypatch.setenv('LABRAG_TIMEOUT_SECONDS','90')
    client=Mock()
    tools=[{'type':'webhook','name':'lookup_lab_test','response_timeout_secs':timeout,
            'api_schema':{'url':'https://example.test/api/v1/tools/lookup_lab_test','method':'POST'}},
           {'type':'webhook','name':'create_appointment','api_schema':{
               'url':'https://example.test/api/v1/tools/create_appointment','method':'POST',
               'request_body_schema':{'properties':{'confirmed':{'type':confirmed_type}}}}}]
    client.conversational_ai.agents.get.return_value.model_dump.return_value={
        'conversation_config':{'agent':{'prompt':{'tools':tools}}},'platform_settings':{'auth':{'enable_auth':True}}}
    if valid:
        require_readonly_agent(client,'offline')
    else:
        with pytest.raises(LiveSetupError):require_readonly_agent(client,'offline')


def test_signed_url_sdk_contract_and_no_credentials_in_result(monkeypatch):
    from elevenlabs.client import ElevenLabs
    monkeypatch.setattr('app.integrations.live_voice.load_dotenv',lambda *a,**k:None)
    monkeypatch.setenv('ELEVENLABS_AGENT_ID','agent_synthetic')
    monkeypatch.setenv('ELEVENLABS_API_KEY','offline-fixture')
    calls=[]
    def handle(request):
        calls.append(request)
        if '/agents/' in request.url.path:
            return httpx.Response(200,json={'agent_id':'agent_synthetic','name':'Synthetic',
                'conversation_config':{'agent':{'prompt':{'tools':[{'type':'webhook','name':'lookup_lab_test',
                'description':'Read only','api_schema':{'url':'https://example.test/api/v1/tools/lookup_lab_test','method':'POST'}}]}}},
                'platform_settings':{'auth':{'enable_auth':True}}})
        assert request.url.params['agent_id']=='agent_synthetic'
        assert request.url.params['include_conversation_id']=='true'
        return httpx.Response(200,json={'signed_url':'wss://api.elevenlabs.io/v1/convai/conversation?conversation_signature=fixture'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result=signed_conversation_url(ElevenLabs(api_key='offline-fixture',httpx_client=http))
    assert result.startswith('wss://') and 'offline-fixture' not in result and len(calls)==2


def test_signing_failure_and_bad_url_sanitized(monkeypatch):
    monkeypatch.setattr('app.integrations.live_voice.live_configuration_present',lambda:True)
    monkeypatch.setenv('ELEVENLABS_AGENT_ID','agent_synthetic')
    monkeypatch.setattr('app.integrations.live_voice.require_readonly_agent',lambda *a:None)
    client=Mock()
    client.conversational_ai.conversations.get_signed_url.side_effect=RuntimeError('PRIVATE KEY')
    with pytest.raises(ValueError) as error:
        signed_conversation_url(client)
    assert 'PRIVATE' not in str(error.value)
    client.conversational_ai.conversations.get_signed_url.side_effect=None
    client.conversational_ai.conversations.get_signed_url.return_value=SimpleNamespace(signed_url='javascript:bad')
    with pytest.raises(ValueError): signed_conversation_url(client)


def test_start_events_require_uuid():
    request=str(uuid4())
    assert valid_start_event({'action':'start','request_id':request})==request
    for value in (None,{}, {'action':'start','request_id':'bad'},{'action':'connected','request_id':request}):
        assert valid_start_event(value) is None


@pytest.mark.parametrize('names,authenticated',[
    ([],True),(['lookup_lab_test','book_appointment'],True),(['lookup_lab_test'],False)])
def test_unready_or_write_capable_agent_cannot_mint(monkeypatch,names,authenticated):
    monkeypatch.setattr('app.integrations.live_voice.live_configuration_present',lambda:True)
    monkeypatch.setenv('ELEVENLABS_AGENT_ID','agent_synthetic')
    client=Mock()
    client.conversational_ai.agents.get.return_value.model_dump.return_value={
        'conversation_config':{'agent':{'prompt':{'tools':[{'name':name} for name in names]}}},
        'platform_settings':{'auth':{'enable_auth':authenticated}}}
    with pytest.raises(ValueError): signed_conversation_url(client)
    client.conversational_ai.conversations.get_signed_url.assert_not_called()


def test_referenced_tool_config_is_read_not_modified(monkeypatch):
    monkeypatch.setattr('app.integrations.live_voice.live_configuration_present',lambda:True)
    monkeypatch.setenv('ELEVENLABS_AGENT_ID','agent_synthetic')
    client=Mock()
    client.conversational_ai.agents.get.return_value.model_dump.return_value={
        'conversation_config':{'agent':{'prompt':{'tool_ids':['tool_fixture']}}},
        'platform_settings':{'auth':{'enable_auth':True}}}
    client.conversational_ai.tools.get.return_value.model_dump.return_value={'tool_config':{
        'type':'webhook','name':'lookup_lab_test','api_schema':{'method':'POST','url':'https://example.test/api/v1/tools/lookup_lab_test'}}}
    client.conversational_ai.conversations.get_signed_url.return_value=SimpleNamespace(signed_url='wss://api.elevenlabs.io/v1/convai/conversation?signature=fixture')
    assert signed_conversation_url(client).startswith('wss://')
    client.conversational_ai.tools.get.assert_called_once()


def test_streamlit_mints_once_and_keeps_other_inputs(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    ui=ui_for(service,monkeypatch)
    event={'action':'start','request_id':str(uuid4())}
    component=Mock(side_effect=lambda **kwargs:event)
    signer=Mock(return_value='wss://api.elevenlabs.io/v1/convai/conversation?signature=fixture')
    monkeypatch.setattr('app.ui.live_voice_panel.live_configuration_present',lambda:True)
    monkeypatch.setattr('app.ui.live_voice_panel.components.declare_component',lambda *a,**k:component)
    monkeypatch.setattr('app.ui.live_voice_panel.signed_conversation_url',signer)
    sign_in(ui)
    assert not ui.exception and ui.chat_input and ui.toggle[0].value is False
    signer.assert_called_once()
    ui.run(timeout=30)
    signer.assert_called_once()
    assert 'conversation' not in ui.session_state
    assert set(component.call_args.kwargs)=={'signed_url','request_id','error','key','default'}
