from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import streamlit
from app.integrations.elevenlabs_voice import VoiceError
from app.integrations.labrag_client import HttpLabRAGClient
from tests.test_labrag import payload
from tests.test_ui import ui_for,sign_in
from tests.test_elevenlabs_voice import recording


def test_voice_off_review_edit_send_sources_and_replay(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json=payload())
    monkeypatch.setattr('app.bootstrap.build_labrag_client',lambda:HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(handle)))
    voice=Mock()
    voice.transcribe.return_value='What specimen is required for BUN?'
    voice.speak.return_value=b'mock-mp3'
    factory=Mock(return_value=voice)
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',factory)
    microphone=Mock(return_value=SimpleNamespace(getvalue=recording))
    monkeypatch.setattr(streamlit,'audio_input',microphone)
    sign_in(ui)
    assert ui.chat_input and ui.toggle[0].value is False
    factory.assert_not_called(); microphone.assert_not_called()
    ui.toggle[0].set_value(True).run(timeout=30)
    assert ui.chat_input and not ui.text_area
    voice.transcribe.assert_not_called()
    next(b for b in ui.button if b.label=='Transcribe recording').click().run(timeout=30)
    assert ui.text_area[0].value=='What specimen is required for BUN?' and not calls
    reviewed='What specimen and volume are required for BUN?'
    ui.text_area[0].set_value(reviewed)
    next(b for b in ui.button if b.label=='Send reviewed question').click().run(timeout=30)
    assert not ui.exception and len(calls)==1
    assert reviewed.encode() in calls[0].content
    assert any(e.label=='View sources' and not e.proto.expanded for e in ui.expander)
    assert any('Laboratory reference answer' in t.value for t in ui.text)
    assert ui.get('audio') and ui.chat_input
    assert 'conversation' not in ui.session_state
    voice.transcribe.assert_called_once(); voice.speak.assert_called_once()
    ui.run(timeout=30)
    assert len(calls)==1 and ui.get('audio')
    voice.transcribe.assert_called_once(); voice.speak.assert_called_once()
    next(b for b in ui.button if b.label=='Log out').click().run(timeout=30)
    assert not ui.get('audio') and not ui.chat_input
    sign_in(ui)
    assert ui.toggle[0].value is False


def test_missing_voice_configuration_keeps_text_chat(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',Mock(side_effect=VoiceError('Missing configuration')))
    sign_in(ui)
    ui.toggle[0].set_value(True).run(timeout=30)
    assert not ui.exception and ui.chat_input
    assert any('Voice is not configured' in item.value for item in ui.info)


def test_microphone_failure_keeps_text_chat(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    voice=Mock()
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',lambda:voice)
    monkeypatch.setattr(streamlit,'audio_input',Mock(side_effect=RuntimeError('permission denied')))
    sign_in(ui)
    ui.toggle[0].set_value(True).run(timeout=30)
    assert not ui.exception and ui.chat_input
    assert any('Microphone unavailable' in item.value for item in ui.info)
    voice.transcribe.assert_not_called()


def test_spoken_confirm_preserves_text_confirmation_in_ui(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    voice=Mock()
    voice.transcribe.return_value='confirm'
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',lambda:voice)
    monkeypatch.setattr(streamlit,'audio_input',lambda *a,**k:SimpleNamespace(getvalue=recording))
    sign_in(ui)
    ui.chat_input[0].set_value('Schedule CBC 2099-01-15 at 11:00 with Jordan').run(timeout=30)
    ui.chat_input[0].set_value('Taylor Example, June 10, 1995').run(timeout=30)
    pending=dict(ui.session_state['conversation']['pending'])
    confirmation=ui.session_state['conversation']['confirmation']
    rows=len(service.repository.appointments())
    ui.toggle[0].set_value(True).run(timeout=30)
    next(b for b in ui.button if b.label=='Transcribe recording').click().run(timeout=30)
    next(b for b in ui.button if b.label=='Send reviewed question').click().run(timeout=30)
    assert ui.session_state['conversation']['pending']==pending
    assert ui.session_state['conversation']['confirmation']==confirmation
    assert len(service.repository.appointments())==rows
    voice.speak.assert_not_called()
    ui.chat_input[0].set_value('confirm').run(timeout=30)
    assert len(service.repository.appointments())==rows+1 and not ui.exception


def test_transcription_failure_keeps_text_chat_and_is_not_retried(service,monkeypatch):
    ui=ui_for(service,monkeypatch)
    voice=Mock()
    voice.transcribe.side_effect=VoiceError('Transcription failed. Use text chat.')
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',lambda:voice)
    monkeypatch.setattr(streamlit,'audio_input',lambda *a,**k:SimpleNamespace(getvalue=recording))
    sign_in(ui)
    ui.toggle[0].set_value(True).run(timeout=30)
    next(b for b in ui.button if b.label=='Transcribe recording').click().run(timeout=30)
    assert ui.chat_input and not ui.text_area and ui.info and not ui.exception
    ui.run(timeout=30)
    voice.transcribe.assert_called_once()


def test_text_answer_is_rendered_while_audio_is_still_pending(service,monkeypatch):
    from threading import Event
    gate=Event()
    ui=ui_for(service,monkeypatch)
    voice=Mock()
    voice.transcribe.return_value='What specimen is required for BUN?'
    voice.speak.side_effect=lambda text:(gate.wait(timeout=20),b'delayed-mp3')[1]
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',lambda:voice)
    monkeypatch.setattr(streamlit,'audio_input',lambda *a,**k:SimpleNamespace(getvalue=recording))
    monkeypatch.setattr('app.bootstrap.build_labrag_client',lambda:HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload()))))
    sign_in(ui)
    try:
        ui.toggle[0].set_value(True).run(timeout=30)
        next(b for b in ui.button if b.label=='Transcribe recording').click().run(timeout=30)
        next(b for b in ui.button if b.label=='Send reviewed question').click().run(timeout=30)
        assert any('Laboratory reference answer' in t.value for t in ui.text)
        assert not ui.get('audio') and any('Preparing spoken answer' in c.value for c in ui.caption)
        assert ui.chat_input and not ui.exception
        gate.set()
        session=ui.session_state['voice_session']
        next(iter(session.audio.values())).result(timeout=3)
        ui.run(timeout=30)
        assert ui.get('audio') and not ui.exception
        voice.speak.assert_called_once()
    finally:
        gate.set()
        if 'voice_session' in ui.session_state:
            ui.session_state['voice_session'].close()
