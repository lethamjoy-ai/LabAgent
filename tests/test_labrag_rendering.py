import json
import httpx
import pytest
from app.integrations.labrag_client import HttpLabRAGClient
from app.ui.sources import prepare_sources
from tests.test_labrag import payload
from tests.test_ui import ui_for, sign_in


def test_distinct_sections_and_safe_clickable_labels():
    original = payload()['citations'][0]
    entries = prepare_sources([original,dict(original,section='Storage',chunk_id='other')])
    assert len(entries) == 2
    assert entries[0]['url'] == entries[1]['url'] == original['source_url']
    assert entries[0]['label'] == '[1] Catalog p. 42 · Specimen'
    assert entries[1]['label'] == '[2] Catalog p. 42 · Storage'
    assert entries[0]['metadata'] == original
    for url in ('javascript:alert(1)','file:///secret.pdf','https://[invalid'):
        assert prepare_sources([dict(original,source_url=url)])[0]['url'] is None


@pytest.mark.parametrize('status',['answered','needs_clarification','insufficient_evidence'])
def test_prepared_bun_sources_and_diagnostics_do_not_requery(service,monkeypatch,status):
    ui = ui_for(service,monkeypatch)
    calls = []
    response = payload(status)
    response['citations'].append(dict(response['citations'][0],section='Storage',chunk_id='storage'))
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=response)
    monkeypatch.setattr('app.bootstrap.build_labrag_client',lambda:HttpLabRAGClient('http://example.test',transport=httpx.MockTransport(handle)))
    sign_in(ui)
    import streamlit
    import app.ui.sources
    from contextlib import contextmanager
    phases = []
    original_spinner = streamlit.spinner
    original_prepare = app.ui.sources.prepare_sources
    original_text = streamlit.text
    @contextmanager
    def spinner(label,*args,**kwargs):
        phases.append('loading_start')
        with original_spinner(label,*args,**kwargs):
            yield
        phases.append('loading_end')
    def prepare(citations):
        if citations:
            phases.append('sources_prepared')
        return original_prepare(citations)
    def text(body,*args,**kwargs):
        if response['answer'] in str(body):
            phases.append('answer_emitted')
        return original_text(body,*args,**kwargs)
    monkeypatch.setattr(streamlit,'spinner',spinner)
    monkeypatch.setattr(streamlit,'text',text)
    monkeypatch.setattr(app.ui.sources,'prepare_sources',prepare)
    ui.chat_input[0].set_value('What specimen is required for BUN?').run(timeout=30)
    assert not ui.exception and len(calls) == 1
    assert phases == ['loading_start','sources_prepared','loading_end','answer_emitted']
    message = ui.session_state['conversation']['history'][-1]
    assert len(message['labrag_citations']) == 2
    shown = '\n'.join(t.value for t in ui.text)
    assert response['answer'] in shown and 'Sources:' not in shown
    source_panel = next(e for e in ui.main.expander if e.label == 'View sources')
    assert not source_panel.proto.expanded
    links = source_panel.get('link_button')
    assert len(links) == 2
    assert links[0].proto.url == links[1].proto.url == response['citations'][0]['source_url']
    assert 'Specimen' in links[0].proto.label and 'Storage' in links[1].proto.label
    assert not any(e.label == 'LabRAG request details' for e in ui.main.expander)
    sidebar = next(e for e in ui.sidebar.expander if e.label == 'Intent routing')
    assert not sidebar.proto.expanded
    assert any(t.value == '**LabRAG request details**' for t in sidebar.markdown)
    timings = dict(ui.session_state['request_timing'])
    assert timings['request_ms'] >= 0 and timings['first_content_to_complete_enqueue_ms'] >= 0
    print('BUN_RENDER_TIMINGS',json.dumps(timings))
    # Expander toggles need no server call. A server rerun is a stronger check.
    ui.run(timeout=30)
    assert len(calls) == 1 and not ui.exception
