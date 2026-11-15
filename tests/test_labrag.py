import json
import httpx
import pytest
from app.integrations.labrag_client import HttpLabRAGClient, LabRAGError, TestInformation as Information
from app.agents.lab_assistant import LabAssistant
from app.appointments.gateway import DirectAppointmentGateway


def payload(status='answered'):
    return {'status':status,'answer':'Which specimen type?' if status=='needs_clarification' else 'Laboratory reference answer [1].',
        'citations':[{'source_id':'s1','document_id':'doc1','document_title':'Catalog','provider':'Mayo','test_code':'BUN',
        'test_name':'Blood urea nitrogen','printed_catalog_page':'42','pdf_page_1_based':'7','original_pdf_page_1_based':'9',
        'section':'Specimen','chunk_id':'chunk-1','source_url':'https://example.test/catalog','citation_role':'primary','future_field':'preserved'}],
        'matched_tests':[{'test_code':'BUN','test_name':'Blood urea nitrogen'}],'request_id':'api-request-1'}


def test_missing_information_is_explicit():
    text = Information(test_id='CBC',test_name='CBC',answer='Unverified answer').display()
    assert 'Sources: Missing' in text and 'Specimen: Not provided' in text and 'Container: Not provided' in text


@pytest.mark.parametrize('status',['answered','needs_clarification','insufficient_evidence'])
def test_contract_status_and_citation_preservation(status):
    calls=[]
    def handle(r):
        calls.append(r)
        assert r.url.path == '/api/v1/query'
        assert json.loads(r.content) == {'question':'What specimen is required for BUN?'}
        return httpx.Response(200,json=payload(status))
    result=HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle)).query('BUN','What specimen is required for BUN?')
    assert len(calls)==1 and result.status==status
    assert result.citations[0].model_dump()['future_field']=='preserved'
    assert result.request_id=='api-request-1'
    for field in ('Printed catalog page: 42','PDF page (1-based): 7','Original PDF page (1-based): 9'):
        assert field in result.display()
    if status=='insufficient_evidence': assert 'insufficient evidence' in result.display()


@pytest.mark.parametrize('status,code',[(422,'http_422'),(503,'http_503'),(504,'http_504'),(500,'http_error')])
def test_http_errors_are_distinct_without_retry(status,code):
    calls=[]
    def handle(r):
        calls.append(r)
        return httpx.Response(status,json={'detail':'not forwarded'})
    client=HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle))
    with pytest.raises(LabRAGError) as err: client.query('BUN','specimen BUN')
    assert err.value.code==code and err.value.http_status==status and len(calls)==1


@pytest.mark.parametrize('error,code',[(httpx.ConnectError,'connection_failed'),(httpx.ReadTimeout,'client_timeout'),(httpx.ConnectTimeout,'client_timeout')])
def test_transport_failure_distinction(error,code):
    calls=[]
    def handle(r):
        calls.append(r)
        raise error('synthetic',request=r)
    with pytest.raises(LabRAGError) as err:
        HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle)).query('BUN','specimen BUN')
    assert err.value.code==code and len(calls)==1


@pytest.mark.parametrize('response',[{}, {'status':'unknown'}, {**payload(),'citations':'invalid'}, {**payload(),'answer':''}])
def test_malformed_response_is_rejected(response):
    with pytest.raises(LabRAGError) as err:
        HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response))).query('BUN','specimen BUN')
    assert err.value.code=='malformed_response'


def test_invalid_json_is_rejected():
    with pytest.raises(LabRAGError) as err:
        HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,text='not JSON'))).query('BUN','specimen BUN')
    assert err.value.code=='malformed_response'


def test_clarification_retains_question_and_never_sends_patient_context(service):
    calls=[]
    def handle(r):
        calls.append(json.loads(r.content)['question'])
        return httpx.Response(200,json=payload('needs_clarification' if len(calls)==1 else 'answered'))
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle)))
    s=a.turn('What specimen is required for BUN? Patient Alex Demo, DOB 1990-01-15.')
    assert not calls and 'No query was sent' in s['response']
    s=a.turn('What specimen is required for BUN?',s)
    assert 'Which specimen type?' in s['response'] and s['labrag_original_question']
    s=a.turn('serum',s)
    assert 'Sources:' in s['response'] and s['labrag_original_question'] is None
    assert 'specimen' in calls[1] and 'BUN' in calls[1] and 'serum' in calls[1]
    assert all('Alex' not in q and '1990' not in q and 'SYN' not in q for q in calls)
    assert len(calls)==2


def test_ui_answer_and_citations_with_mock_http(service,monkeypatch):
    from tests.test_ui import ui_for,sign_in
    ui=ui_for(service,monkeypatch)
    monkeypatch.setattr('app.bootstrap.build_labrag_client',lambda:HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload()))))
    sign_in(ui)
    ui.chat_input[0].set_value('What specimen is required for BUN?').run(timeout=30)
    assert not ui.exception
    displayed='\n'.join(item.value for item in ui.text)
    assert 'Laboratory reference answer [1]' in displayed
    assert 'Sources:' not in displayed
    sources = next(e for e in ui.main.expander if e.label == 'View sources')
    assert not sources.proto.expanded
    metadata = ui.session_state['conversation']['history'][-1]['labrag_citations'][0]
    assert metadata['printed_catalog_page'] == '42' and metadata['pdf_page_1_based'] == '7'
    assert metadata['original_pdf_page_1_based'] == '9' and metadata['future_field'] == 'preserved'
    assert 'api-request-1' not in displayed  # technical ID belongs in collapsed Activity JSON


def test_multiple_clarification_turns_preserve_safe_context(service):
    calls=[]
    def handle(r):
        calls.append(json.loads(r.content)['question'])
        return httpx.Response(200,json=payload('needs_clarification' if len(calls)<3 else 'answered'))
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle)))
    s=a.turn('What specimen is required for BUN?')
    s=a.turn('serum',s)
    s=a.turn('adult',s)
    assert 'serum' in calls[-1] and 'adult' in calls[-1] and 'BUN' in calls[-1]
    assert s['labrag_original_question'] is None


def test_insufficient_evidence_is_not_reported_as_verified_answer(service):
    a=LabAssistant(DirectAppointmentGateway(service,service.test_access),HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={**payload('insufficient_evidence'),'answer':'No collection instructions were found.','citations':[]}))))
    s=a.turn('What specimen is required for BUN?')
    assert 'insufficient evidence' in s['response'] and 'No collection instructions were found.' in s['response']
    assert not s.get('labrag_original_question')


def test_runtime_factory_defaults_to_real_client(monkeypatch):
    from app.bootstrap import build_labrag_client
    monkeypatch.delenv('LABRAG_BASE_URL',raising=False)
    monkeypatch.delenv('LABRAG_TIMEOUT_SECONDS',raising=False)
    monkeypatch.setenv('LABRAG_MODE','mock')  # obsolete setting cannot select a fixture
    client=build_labrag_client()
    assert isinstance(client,HttpLabRAGClient) and client.base_url=='http://127.0.0.1:8000' and client.timeout==90
