"""Explicit record, review and Send controls. No audio or transcript logging."""
import streamlit as st
from app.agents.voice_session import VoiceSession
from app.integrations.elevenlabs_voice import ElevenLabsVoice, VoiceError


def discard_voice():
    session = st.session_state.pop("voice_session",None)
    if session:
        session.close()
    for key in list(st.session_state):
        if key.startswith("voice_"):
            st.session_state.pop(key,None)


def next_recording(session, epoch):
    """Remount only draft widgets; keep history and completed/pending audio."""
    session.cancel()
    st.session_state.voice_mic_epoch = epoch+1
    # Old epoch widget values are cleaned up by Streamlit on the next run.
    st.rerun()


def render_voice(assistant, auth, token, render_message):
    # Default False is widget/session-local, never a global preference.
    session = st.session_state.get("voice_session")
    if session and session.owner is not assistant:
        discard_voice()
        session = None
    if session and not hasattr(session,"failed_recordings"):
        # Streamlit hot reload can retain an instance of the old class. Upgrade
        # it in place rather than discarding this chat and its replay audio.
        epoch = st.session_state.get("voice_mic_epoch",0)
        session.__class__ = VoiceSession
        session.recordings = {(epoch,value) for value in session.recordings}
        session.failed_recordings = set(session.recordings) if session.error else set()
        if session.draft_id:
            st.session_state[f"voice_review_{epoch}"] = st.session_state.get("voice_review",session.transcript)
        elif session.state.get("history"):
            st.session_state.voice_mic_epoch = epoch+1
    enabled = st.sidebar.toggle("Enable voice",value=False,key="voice_enabled")
    if session:
        for message in session.state.get("history",[]):
            render_message(message)
        _render_audio(session)
    if not enabled:
        return
    st.caption("Voice demo: general lab catalog questions only. Use synthetic examples only; do not record names, birth dates, or patient information. Audio is sent to ElevenLabs when you choose Transcribe; answer text is sent there for playback. For example: What specimen is required for BUN?")
    if session is None:
        try:
            session = VoiceSession(assistant,ElevenLabsVoice())
            st.session_state.voice_session = session
        except (VoiceError,ValueError,ImportError):
            st.info("Voice is not configured. Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID on the server, or use text chat.")
            return
    epoch = st.session_state.get("voice_mic_epoch",0)
    try:
        label = "Record next question" if epoch else "Record a demo general lab question (start / stop)"
        recording = st.audio_input(label,sample_rate=16000,key=f"voice_recording_{epoch}")
    except Exception:
        st.info("Microphone unavailable or permission denied. Use text chat, or check your browser microphone permission.")
        if st.button("Start a new recording",key=f"voice_reset_{epoch}"):
            next_recording(session,epoch)
        return
    st.caption("The mic shows recording activity; press Stop when finished. If microphone permission is denied or unavailable, use text chat. Record no more than 30 seconds.")
    if st.button("Cancel recording / transcript",key="voice_cancel"):
        next_recording(session,epoch)
    retry = bool(session.error and not session.draft_id)
    if st.button("Retry transcription" if retry else "Transcribe recording",key=f"voice_transcribe_{epoch}",disabled=recording is None):
        if not auth.current(token):
            st.warning("Please sign in again. Use text chat after signing in.")
            return
        with st.spinner("Transcribing recording..."):
            changed = session.transcribe(recording.getvalue(),attempt_id=epoch,retry=retry)
        if changed and session.draft_id:
            st.session_state[f"voice_review_{epoch}"] = session.transcript
        elif not changed:
            st.info("This recording was already processed. Review its transcript or record a new question.")
    if session.error:
        st.info(session.error)
    if session.draft_id:
        with st.form(f"voice_review_form_{epoch}"):
            reviewed = st.text_area("Review and edit transcript",key=f"voice_review_{epoch}",max_chars=3000)
            sent = st.form_submit_button("Send reviewed question")
        if sent:
            if not auth.current(token):
                st.warning("Please sign in again before sending. Text chat remains available after sign-in.")
                return
            with st.spinner("Checking laboratory references..."):
                submitted = session.submit(reviewed,session.draft_id)
            if submitted:
                st.session_state.latest_request_channel = "voice"
                # Emit answer/citations BEFORE scheduling speech. TTS has no UI
                # calls and its failure cannot suppress the returned answer.
                if session.state.get("history"):
                    for message in session.state["history"][-2:]:
                        render_message(message)
                session.start_audio()
                next_recording(session,epoch)


def _render_audio(session):
    pending = any(not future.done() for future in session.audio.values())

    @st.fragment(run_every=1 if pending else None)
    def playback():
        if not session.assistant.tools.gateway.principal():
            return
        still_pending = False
        for index, future in enumerate(session.audio.values(),1):
            if not future.done():
                st.caption("Preparing spoken answer; the text answer is ready.")
                still_pending = True
            else:
                audio,error = future.result()
                if error:
                    st.caption(error)
                elif audio:
                    st.caption(f"Voice answer {index} · Play or replay")
                    st.audio(audio,format="audio/mpeg",autoplay=False)
        if pending and not still_pending:
            # Stop the polling fragment after completion; rerun only renders.
            st.rerun()

    playback()
