"""Streamlit component bridge. Only a short-lived signed capability reaches JS."""
from pathlib import Path
from time import monotonic
import streamlit as st
import streamlit.components.v1 as components
from app.integrations.live_voice import live_configuration_present, signed_conversation_url, valid_start_event

BUILD = Path(__file__).parent/"live_voice_frontend"/"dist"


def render_live_voice(auth, token):
    st.caption("Live conversation is separate from reviewed voice and cannot change your text booking. Catalogue questions and synthetic demo appointments only. Never share patient details.")
    if not live_configuration_present():
        st.info("Live voice needs ELEVENLABS_AGENT_ID and ELEVENLABS_API_KEY on the server. Text and reviewed voice remain available.")
        return
    if not BUILD.exists():
        st.info("Live voice frontend needs its local build. Text and reviewed voice remain available; see docs/live-voice.md.")
        return
    component = components.declare_component("labassist_live_voice",path=str(BUILD))
    current = st.session_state.setdefault("live_voice_session",{"seen":set()})
    if current.get("url") and monotonic()-current.get("created",0)>60:
        current.pop("url",None)
        current["error"] = True
    event = component(signed_url=current.get("url"),request_id=current.get("request_id"),
        error=current.get("error",False),key="live_voice_component",default=None)
    request_id = valid_start_event(event)
    if request_id and request_id not in current["seen"]:
        current["seen"].add(request_id)
        current.update(request_id=request_id,error=False)
        if not auth.current(token):
            current["error"] = True
        else:
            try:
                current.update(url=signed_conversation_url(),created=monotonic())
            except ValueError as exc:
                current.pop("url",None)
                current["error"] = True
                current["message"] = str(exc)
        st.rerun()
    elif isinstance(event,dict) and event.get("action") in ("connected","ended","failed"):
        current.pop("url",None)
    if current.get("error"):
        st.info(current.get("message","Live voice is unavailable. Use text or reviewed voice."))
