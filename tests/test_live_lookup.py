from unittest.mock import Mock
import json
import logging
import httpx
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.integrations.labrag_client import HttpLabRAGClient
from tests.test_labrag import payload

SECRET='synthetic-webhook-secret-32-characters-long'
HEADERS={'X-LabAssist-Webhook-Secret':SECRET}


@pytest.fixture
def tool():
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=payload())
    api=create_app(HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(handle)),SECRET)
    return TestClient(api),calls


@pytest.mark.parametrize('headers',[{}, {'X-LabAssist-Webhook-Secret':'wrong'}])
def test_authentication_precedes_lookup(tool,headers):
    client,calls=tool
    assert client.post('/api/v1/tools/lookup_lab_test',headers=headers,json={'question':'BUN specimen?'}).status_code==401
    assert not calls


def test_missing_configuration_fails_closed():
    with TestClient(create_app(Mock(),secret='')) as client:
        assert client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,json={'question':'BUN?'}).status_code==503


@pytest.mark.parametrize('body',[{}, {'question':''},{'question':'   '},{'question':42},{'question':'x'*3001},{'question':'BUN?','patient':'private'}, {'question':'confirm'}, {'question':'Show my schedule today'},{'question':'Patient Name: Private Example, DOB 1999-01-01, BUN?'}])
def test_validation_and_scope_do_not_query_or_echo_input(tool,body):
    client,calls=tool
    response=client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,json=body)
    assert response.status_code==422 and not calls
    assert 'Private Example' not in response.text and '1999-01-01' not in response.text


def test_invalid_json_and_body_limit(tool):
    client,calls=tool
    assert client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,content='{broken').status_code==422
    assert client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,content=b'x'*17000).status_code==413
    assert not calls
    assert client.post('/api/v1/tools/lookup_lab_test',content='{broken').status_code==401


@pytest.mark.parametrize('status',['answered','needs_clarification','insufficient_evidence'])
def test_status_and_lossless_sources(status):
    api=create_app(HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload(status)))),SECRET)
    with TestClient(api) as client:
        result=client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,json={'question':'What specimen is required for BUN?'}).json()
    assert result['evidence_status']==status
    assert result['sources'][0]['printed_catalog_page']=='42'
    assert result['sources'][0]['pdf_page_1_based']=='7'
    assert result['sources'][0]['future_field']=='preserved'


def test_two_mocked_catalogue_lookups_one_call_each_no_question_logs(tool,caplog):
    client,calls=tool
    questions=['What specimen is required for BUN?','What volume is required for BUN?']
    with caplog.at_level(logging.INFO,logger='labassist.lookup'):
        for question in questions:
            assert client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,json={'question':question}).status_code==200
    assert calls==[{'question':q} for q in questions]
    assert client.app.state.lookup_completed==2
    messages=[record.message for record in caplog.records if record.name=='labassist.lookup']
    assert len(messages)==2 and all('completed call_id=' in message for message in messages)
    assert all(q not in caplog.text for q in questions) and SECRET not in caplog.text


@pytest.mark.parametrize('status',[422,503,504])
def test_upstream_errors_are_safe_and_not_retried(status):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(status,json={'detail':'PRIVATE PROVIDER BODY'})
    with TestClient(create_app(HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(handle)),SECRET)) as client:
        result=client.post('/api/v1/tools/lookup_lab_test',headers=HEADERS,json={'question':'BUN specimen?'})
    assert result.status_code==status and 'PRIVATE' not in result.text and len(calls)==1
