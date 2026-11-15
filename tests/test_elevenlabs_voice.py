from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import wave
import pytest
from app.integrations.elevenlabs_voice import ElevenLabsVoice, VoiceSettings, VoiceError, spoken_excerpt


def recording(seconds=0.2):
    data=BytesIO()
    with wave.open(data,'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b'\0\0'*int(16000*seconds))
    return data.getvalue()


def test_sdk_arguments_and_no_automatic_retries():
    client=Mock()
    client.speech_to_text.convert.return_value=SimpleNamespace(text='What specimen is required for BUN?')
    client.text_to_speech.convert.return_value=iter([b'mp3',b'bytes'])
    voice=ElevenLabsVoice(VoiceSettings('synthetic-voice'),client)
    assert voice.transcribe(recording())=='What specimen is required for BUN?'
    kwargs=client.speech_to_text.convert.call_args.kwargs
    assert kwargs['model_id']=='scribe_v2' and kwargs['request_options']=={'max_retries':0}
    assert kwargs['diarize'] is False and kwargs['file'].getvalue()==recording()
    assert voice.speak('Serum is required.')==b'mp3bytes'
    kwargs=client.text_to_speech.convert.call_args.kwargs
    assert kwargs['voice_id']=='synthetic-voice' and kwargs['output_format']=='mp3_44100_128'
    assert kwargs['request_options']=={'max_retries':0}


@pytest.mark.parametrize('data',[b'',b'not-wav',recording(0),recording(31),b'x'*2_000_001])
def test_invalid_or_empty_recordings_do_not_call_provider(data):
    client=Mock()
    with pytest.raises(VoiceError):
        ElevenLabsVoice(VoiceSettings('demo'),client).transcribe(data)
    client.speech_to_text.convert.assert_not_called()


def test_failures_are_sanitized_and_empty_transcripts_rejected():
    client=Mock()
    voice=ElevenLabsVoice(VoiceSettings('demo'),client)
    client.speech_to_text.convert.side_effect=RuntimeError('PRIVATE PROVIDER BODY')
    with pytest.raises(VoiceError,match='Transcription failed') as error:
        voice.transcribe(recording())
    assert 'PRIVATE' not in str(error.value)
    client.speech_to_text.convert.side_effect=None
    client.speech_to_text.convert.return_value=SimpleNamespace(text=' ')
    with pytest.raises(VoiceError,match='No usable transcript'):
        voice.transcribe(recording())
    client.text_to_speech.convert.side_effect=RuntimeError('PRIVATE PROVIDER BODY')
    with pytest.raises(VoiceError,match='Audio generation failed'):
        voice.speak('Reference answer')


def test_spoken_excerpt_keeps_complete_instructions_and_removes_links():
    text='Use 1.5 mL serum [1]. See [catalog](https://example.test/manual.pdf).\nrequest_id: private-id\nSources:\nhttps://example.test'
    spoken=spoken_excerpt(text)
    assert '1.5 mL serum' in spoken and 'https' not in spoken and '[1]' not in spoken
    assert 'private-id' not in spoken and 'Sources:' not in spoken
    assert len(spoken_excerpt('A long complete sentence. '*100))<600
    assert 'full laboratory answer' in spoken_excerpt('x'*1000)


def test_missing_configuration(monkeypatch):
    monkeypatch.setattr('app.integrations.elevenlabs_voice.load_dotenv',lambda *a,**k:None)
    monkeypatch.delenv('ELEVENLABS_VOICE_ID',raising=False)
    with pytest.raises(VoiceError,match='Text chat is available'):
        VoiceSettings.configured()


def test_installed_sdk_contract_with_mock_http():
    import httpx
    from elevenlabs.client import ElevenLabs
    calls=[]
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/speech-to-text'):
            assert b'scribe_v2' in request.content
            return httpx.Response(200,json={'text':'What specimen is required for BUN?',
                'language_code':'eng','language_probability':1.0,'words':[]})
        assert '/text-to-speech/synthetic-voice' in request.url.path
        return httpx.Response(200,content=b'offline-mp3',headers={'content-type':'audio/mpeg'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        voice=ElevenLabsVoice(VoiceSettings('synthetic-voice'),ElevenLabs(api_key='offline-fixture',httpx_client=http))
        assert voice.transcribe(recording())=='What specimen is required for BUN?'
        assert voice.speak('Serum is required.')==b'offline-mp3'
    assert len(calls)==2


def test_installed_sdk_does_not_retry_failure():
    import httpx
    from elevenlabs.client import ElevenLabs
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(503,json={'detail':'offline failure'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        voice=ElevenLabsVoice(VoiceSettings('demo'),ElevenLabs(api_key='offline-fixture',httpx_client=http))
        with pytest.raises(VoiceError):
            voice.transcribe(recording())
    assert len(calls)==1
