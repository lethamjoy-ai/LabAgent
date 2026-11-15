from copy import deepcopy
from unittest.mock import Mock
import httpx
import pytest
from app.agents.voice_session import VoiceSession, TEXT_ONLY
from app.agents.planner import Plan
from app.appointments.models import GatewayRequest
from app.integrations.labrag_client import HttpLabRAGClient
from app.integrations.elevenlabs_voice import VoiceError
from tests.test_labrag import payload
from tests.test_elevenlabs_voice import recording


def configured(assistant, text='What specimen is required for BUN?'):
    voice=Mock()
    voice.transcribe.return_value=text
    voice.speak.return_value=b'mp3-fixture'
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json=payload())
    assistant.labrag=HttpLabRAGClient('http://fixture.test',transport=httpx.MockTransport(handle))
    return VoiceSession(assistant,voice),voice,calls


def test_record_review_send_and_audio_are_each_once(assistant):
    session,voice,calls=configured(assistant)
    assert session.transcribe(recording()) and session.draft_id
    assert not calls  # Transcription is not permission to query.
    draft=session.draft_id
    assert not session.transcribe(recording())
    voice.transcribe.assert_called_once()
    assert session.submit('What specimen and volume are required for BUN?',draft)
    assert len(calls)==1 and session.state['history'][-1]['labrag_citations']
    assert not session.submit('confirm',draft) and len(calls)==1
    assert not session.audio  # Text is ready independently of TTS.
    session.start_audio(); session.start_audio()
    for future in session.audio.values():
        assert future.result(timeout=3)==(b'mp3-fixture',None)
    voice.speak.assert_called_once()
    assert 'https://' not in voice.speak.call_args.args[0]
    session.close()


@pytest.mark.parametrize('question',['Schedule CBC tomorrow at 1 PM','confirm','Show my schedule today','Find patient records','Show available appointment slots'])
def test_voice_cannot_change_pending_text_booking_even_with_bad_router(assistant,service,question):
    # A deliberately misclassifying router must not defeat the voice guard.
    text_state=assistant.turn('Schedule CBC 2099-01-15 at 11:00 with Jordan')
    text_state=assistant.turn('Taylor Example, June 10, 1995',text_state)
    assert text_state['confirmation']
    before=deepcopy(text_state)
    registry=(assistant._active_action,assistant._matched_patient,assistant._active_confirmation)
    rows=[a.model_dump() for a in service.repository.appointments()]
    session,voice,calls=configured(assistant,question)
    session.assistant.planner.planner=Mock()
    session.assistant.planner.planner.classify.return_value=Plan(intent='information')
    gateway=Mock(wraps=assistant.tools.gateway)
    session.assistant.tools.gateway.original=gateway
    session.transcribe(recording())
    session.submit(question,session.draft_id)
    assert session.state['response']==TEXT_ONLY and not calls
    gateway.execute.assert_not_called()
    assert text_state==before
    assert registry==(assistant._active_action,assistant._matched_patient,assistant._active_confirmation)
    assert rows==[a.model_dump() for a in service.repository.appointments()]
    # The original text confirmation still works with its own current proof.
    result=assistant.turn('confirm',text_state)
    assert 'successfully scheduled' in result['response']
    assert len(service.repository.appointments())==len(rows)+1


@pytest.mark.parametrize('intent',['booking','confirm','overview','patient','lookup','availability','admin_overview'])
def test_router_cannot_dispatch_patient_tools_from_voice(assistant,intent):
    session,voice,calls=configured(assistant)
    session.planner.planner=Mock()
    session.planner.planner.classify.return_value=Plan(intent=intent)
    session.transcribe(recording())
    session.submit('What specimen is required for BUN?',session.draft_id)
    assert session.state['response']==TEXT_ONLY and not calls and not session.state['tool_results']
    with pytest.raises(PermissionError):
        session.assistant.tools.gateway.execute(GatewayRequest(action='overview',day='2030-01-15'))


def test_transcription_and_audio_errors_do_not_retry(assistant):
    session,voice,calls=configured(assistant)
    voice.transcribe.side_effect=VoiceError('Transcription failed. Use text chat.')
    assert session.transcribe(recording()) and session.error
    assert not session.transcribe(recording())
    voice.transcribe.assert_called_once()
    assert not session.draft_id and not calls
    voice.transcribe.side_effect=None
    session.transcribe(recording(0.3))
    session.submit(session.transcript,session.draft_id)
    answer=session.state['response']
    voice.speak.side_effect=RuntimeError('private provider failure')
    session.start_audio()
    future=next(iter(session.audio.values()))
    assert future.result(timeout=3)[0] is None
    session.start_audio()
    assert session.state['response']==answer and len(calls)==1
    voice.speak.assert_called_once()
    session.close()


def test_old_automatic_adapter_cannot_bypass_review_or_scope(assistant):
    from app.voice.adapter import VoiceAdapter
    speech=Mock()
    with pytest.raises(RuntimeError,match='Automatic voice submission is disabled'):
        VoiceAdapter(assistant,speech).turn(b'synthetic-audio',{'confirmation':'old-proof'})
    speech.transcribe.assert_not_called()
    speech.synthesize.assert_not_called()
