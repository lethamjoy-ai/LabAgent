from time import perf_counter
from tests.test_ui import ui_for, sign_in

def test_measure_ui(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    t=perf_counter(); sign_in(ui); print('LOGIN_MS',round((perf_counter()-t)*1000,2))
    for question in ('Show my schedule 2030-01-15','Show my schedule 2030-01-15','Show my schedule today'):
        t=perf_counter(); ui.chat_input[0].set_value(question).run(timeout=30)
        print('SCHEDULE_MS',round((perf_counter()-t)*1000,2))
        print('SERVER_STAGES',dict(ui.session_state['request_timing']))
    print('LOGIN_STAGES',dict(ui.session_state['login_timing']))
    assert not ui.exception


def test_same_turn_render_and_diagnostics_do_not_repeat_work(service,monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tests.mock_router import OfflineRouter
    import app.bootstrap
    import streamlit
    original = app.bootstrap.create_assistant
    calls = []
    reruns = []
    original_rerun = streamlit.rerun
    def rerun(*args,**kwargs):
        reruns.append(1)
        return original_rerun(*args,**kwargs)
    monkeypatch.setattr(streamlit,'rerun',rerun)
    class Router(OfflineRouter):
        def classify(self,*args,**kwargs):
            calls.append(1)
            return super().classify(*args,**kwargs)
    def build(*args,**kwargs):
        assistant = original(*args,**kwargs)
        assistant.planner = Router()
        assistant.now = lambda: datetime(2030,1,15,tzinfo=ZoneInfo('America/Los_Angeles'))
        return assistant
    monkeypatch.setattr(app.bootstrap,'create_assistant',build)
    ui = ui_for(service,monkeypatch)
    sign_in(ui,password='incorrect')
    assert ui.error and not ui.chat_input
    sign_in(ui)
    assert ui.chat_input and not ui.text_input
    assert not reruns
    assert not any(e.label == 'Demo configuration' for e in ui.expander)
    ui.chat_input[0].set_value('Show my schedule today').run(timeout=30)
    assert len(calls) == 1 and len(ui.dataframe) == 1
    assert not reruns
    state = ui.session_state['conversation']
    assert state['history'][-1]['overview_rows'] and state['response'] == state['history'][-1]['content']
    assistant_messages = [message for message in ui.chat_message if message.name == 'assistant']
    assert len(assistant_messages[-1].dataframe) == 1
    assert assistant_messages[-1].text[0].value == state['response']
    assert len(state['tool_results']) == 1
    assert list(ui.dataframe[0].value.columns) == ['Patient Name','Date of Birth','Laboratory Test','Appointment Time','Status']
    assert len([e for e in ui.sidebar.expander if e.label == 'Intent routing']) == 1
    assert not ui.main.expander
    for value in ui.session_state['request_timing'].values():
        assert isinstance(value,(int,float)) and value >= 0
    # Opening an expander is frontend-only; even a server rerun must not replay.
    ui.run(timeout=30)
    assert len(calls) == 1 and len(ui.dataframe) == 1
    next(b for b in ui.button if b.label == 'Log out').click().run(timeout=30)
    assert not ui.chat_input and not ui.dataframe and ui.text_input
    sign_in(ui,'jordan.demo')
    ui.chat_input[0].set_value('Show my schedule today').run(timeout=30)
    assert not ui.dataframe and '0 appointments' in ui.session_state['conversation']['response']
    assert any('No assigned appointments' in c.value for c in ui.caption)
    assert len(calls) == 2 and not ui.exception
