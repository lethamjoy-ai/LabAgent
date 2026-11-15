from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
import streamlit
from app.integrations.elevenlabs_voice import VoiceError
from app.integrations.labrag_client import HttpLabRAGClient
from tests.test_elevenlabs_voice import recording
from tests.test_labrag import payload
from tests.test_ui import ui_for, sign_in


def click(ui,label):
    next(b for b in ui.button if b.label==label).click().run(timeout=30)
    assert not ui.exception


def setup(service,monkeypatch,second_status='answered',fail_audio=False):
    ui=ui_for(service,monkeypatch)
    uploads={'voice_recording_0':recording()}
    observed=[]
    def mic(label,**kwargs):
        key=kwargs['key']; observed.append((key,label))
        data=uploads.get(key)
        return SimpleNamespace(getvalue=lambda:data) if data is not None else None
    monkeypatch.setattr(streamlit,'audio_input',mic)
    voice=Mock()
    voice.transcribe.return_value='What specimen is required for BUN?'
    voice.speak.side_effect=[b'first-audio',VoiceError('Audio failed') if fail_audio else b'second-audio']
    monkeypatch.setattr('app.ui.voice_panel.ElevenLabsVoice',lambda:voice)
    queries=[]
    def handle(request):
        queries.append(request)
        result=payload(second_status if len(queries)==2 else 'answered')
        result['answer']=f'Synthetic catalog reply {len(queries)}.'
        return httpx.Response(200,json=result)
    monkeypatch.setattr('app.bootstrap.build_labrag_client',lambda:HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(handle)))
    sign_in(ui)
    ui.toggle[0].set_value(True).run(timeout=30)
    return ui,voice,uploads,observed,queries


@pytest.mark.parametrize('second_question,status,fail_audio',[
    ('What specimen is required for BUN?','answered',False),
    ('What specimen is required for CBC?','insufficient_evidence',False),
    ('What specimen is required for CBC?','answered',True),
])
def test_two_questions_get_fresh_recorder_and_keep_first_audio(service,monkeypatch,second_question,status,fail_audio):
    ui,voice,uploads,observed,queries=setup(service,monkeypatch,status,fail_audio)
    for index,question in enumerate(('What specimen is required for BUN?',second_question)):
        key=f'voice_recording_{index}'
        uploads[key]=recording()  # Identical bytes are valid in a NEW attempt.
        voice.transcribe.return_value=question
        ui.run(timeout=30)
        click(ui,'Transcribe recording')
        assert ui.text_area[0].value==question
        draft=ui.session_state['voice_session'].draft_id
        ui.text_area[0].set_value(question+' Please.').run(timeout=30)
        assert observed[-1][0]==key and ui.session_state['voice_session'].draft_id==draft
        click(ui,'Send reviewed question')
        session=ui.session_state['voice_session']
        for future in session.audio.values():
            future.result(timeout=3)
        ui.run(timeout=30)
        assert not ui.text_area and session.draft_id is None and session.transcript==''
        assert observed[-1]==(f'voice_recording_{index+1}','Record next question')
        assert next(b for b in ui.button if b.label=='Transcribe recording').disabled
        assert len(queries)==index+1 and voice.transcribe.call_count==index+1
        assert voice.speak.call_count==index+1
        assert session.audio[next(iter(session.audio))].result()[0]==b'first-audio'
        assert not session.submit(question,draft)  # Late/repeated Send is stale.
    assert len(session.state['history'])==4
    assert len([e for e in ui.expander if e.label=='View sources'])==2
    assert len(ui.get('audio'))==(1 if fail_audio else 2)
    if fail_audio:
        assert any('Audio generation failed' in c.value for c in ui.caption)
    ui.run(timeout=30)
    assert len(queries)==2 and voice.transcribe.call_count==2 and voice.speak.call_count==2
    session.close()


def test_cancel_resets_only_draft_and_explicit_retry_is_available(service,monkeypatch):
    ui,voice,uploads,observed,queries=setup(service,monkeypatch)
    voice.transcribe.side_effect=VoiceError('Transcription failed. Use text chat.')
    click(ui,'Transcribe recording')
    ui.run(timeout=30)
    assert voice.transcribe.call_count==1 and not queries
    assert any(b.label=='Retry transcription' for b in ui.button)
    voice.transcribe.side_effect=None
    click(ui,'Retry transcription')
    assert ui.text_area and voice.transcribe.call_count==2
    assert observed[-1][0]=='voice_recording_0'
    click(ui,'Cancel recording / transcript')
    assert not ui.text_area and observed[-1]==('voice_recording_1','Record next question')
    assert not queries
    uploads['voice_recording_1']=recording()
    ui.run(timeout=30)
    click(ui,'Transcribe recording')
    click(ui,'Send reviewed question')
    assert len(queries)==1 and voice.transcribe.call_count==3
    ui.session_state['voice_session'].close()


def test_widget_error_has_fresh_key_recovery(service,monkeypatch):
    ui,voice,uploads,observed,queries=setup(service,monkeypatch)
    def broken(label,**kwargs):
        if kwargs['key']=='voice_recording_0':
            raise RuntimeError('stale widget')
        observed.append((kwargs['key'],label))
        return None
    monkeypatch.setattr(streamlit,'audio_input',broken)
    ui.run(timeout=30)
    click(ui,'Start a new recording')
    assert observed[-1]==('voice_recording_1','Record next question')
    assert ui.chat_input and not queries
    voice.transcribe.assert_not_called()


def test_hot_reload_upgrades_existing_session_without_losing_reply(service,monkeypatch):
    ui,voice,uploads,observed,queries=setup(service,monkeypatch)
    click(ui,'Transcribe recording')
    click(ui,'Send reviewed question')
    session=ui.session_state['voice_session']
    next(iter(session.audio.values())).result(timeout=3)
    history=list(session.state['history'])
    # Model the pre-fix session that Streamlit retains across a file reload.
    session.recordings={value[1] for value in session.recordings}
    del session.failed_recordings
    session.__class__=type('LegacyVoiceSession',(),{})
    ui.session_state['voice_mic_epoch']=0
    ui.run(timeout=30)
    assert not ui.exception and session.state['history']==history
    assert observed[-1]==('voice_recording_1','Record next question')
    assert ui.get('audio') and len(queries)==1
    session.close()
