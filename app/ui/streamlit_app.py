import streamlit as st
from app.ui.table_renderer import ready as table_renderer_ready
from app.ui.sources import prepare_sources
from app.ui.voice_panel import render_voice, discard_voice
from app.ui.live_voice_panel import render_live_voice
from app.bootstrap import create_assistant, create_auth
import os
from time import perf_counter
from app.appointments.access import AccessContext

st.set_page_config(page_title="LabAssist",page_icon="🧪")
st.title("🧪 LabAssist")
st.caption("Laboratory staff workspace · Synthetic-data demonstration")
if "auth" not in st.session_state:
    st.session_state.auth = create_auth()
auth = st.session_state.auth


def clear_login():
    auth.logout(st.session_state.get("auth_token"))
    discard_voice()
    st.session_state.clear()


principal = auth.current(st.session_state.get("auth_token"))
if not principal:
    discard_voice()
    st.session_state.pop("live_voice_session",None)
    st.session_state.pop("live_voice_component",None)
    had_session = bool(st.session_state.get("auth_token"))
    for key in ("auth_token","staff","assistant","conversation","principal_key"):
        st.session_state.pop(key,None)
    if had_session:
        st.warning("Your session has expired. Please sign in again.")
    login_panel = st.empty()
    with login_panel.container():
        st.subheader("Staff sign in")
        with st.form("staff_login",clear_on_submit=True):
            username = st.text_input("Username")
            password = st.text_input("Password",type="password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            credential_started = perf_counter()
            token = auth.login(username,password)
            del username, password
            verified_at = perf_counter()
            st.session_state.login_timing = {"credential_verification_ms":round((verified_at-credential_started)*1000,2)}
            if token:
                st.session_state.auth_token = token
                principal = auth.current(token)
                st.session_state.authenticated_at = verified_at
            else:
                st.error("Invalid credentials or inactive account.")
        st.caption("Local setup: python -m scripts.setup_staff. Passwords are entered privately; no default credentials exist.")
    if not principal:
        st.stop()
    login_panel.empty()

st.session_state.staff = principal.model_dump()
if st.session_state.get("principal_key") != principal.id:
    st.session_state.pop("live_voice_session",None)
    st.session_state.pop("live_voice_component",None)
    st.session_state.pop("conversation",None)
    st.session_state.pop("assistant",None)
    st.session_state.principal_key = principal.id
with st.sidebar:
    default_provider = os.getenv("LAB_PLANNER","openai")
    provider = st.selectbox("Routing LLM provider", ["openai","groq"],
        index=1 if default_provider == "groq" else 0,
        format_func=lambda value:{"openai":"OpenAI","groq":"Groq"}[value], key="router_provider")
if "assistant" not in st.session_state or getattr(st.session_state.assistant,"router_provider",None) != provider:
    try:
        initialization_started = perf_counter()
        with st.spinner("Loading workspace..."):
            replacement = create_assistant(AccessContext(session_token=st.session_state.auth_token), provider=provider)
        st.session_state.initialization_ms = round((perf_counter()-initialization_started)*1000,2)
    except (ValueError, ImportError):
        label = {"openai":"OpenAI","groq":"Groq"}[provider]
        st.error(f"{label} router is not configured. Check its API key and model settings in .env, or select another provider.")
        st.stop()
    st.session_state.assistant = replacement
assistant = st.session_state.assistant
with st.sidebar:
    st.success(f"{principal.name} · {principal.role}")
    if st.button("Log out"):
        clear_login()
        st.rerun()
    st.subheader("Try asking")
    st.code("Show my schedule today\nWhat appointments do I have tomorrow?\nSchedule CBC tomorrow at 1 PM with Jordan\nWhat specimen is required for BUN?")
    if principal.role == "Admin":
        st.code("Show the organization-wide overview today")
    st.caption("Booking matches patients by full name and DOB. Working hours are configured per staff member.")
    if st.button("New conversation"):
        discard_voice()
        st.session_state.pop("conversation",None)
        st.rerun()
state = st.session_state.get("conversation",{})
def prepare_message(message):
    # Do cold dataframe imports/conversion before emitting the summary. No result
    # data is shared or cached; both elements use this one authorized message.
    preparation_started = perf_counter()
    tables = {}
    for key in ("overview_rows","appointment_rows"):
        if message.get(key):
            table_renderer_ready.result()
            import pandas  # Streamlit's dataframe renderer imports this as well.
            import pyarrow as pa
            tables[key] = pa.Table.from_pylist(message[key])
    table_preparation_ms = (perf_counter()-preparation_started)*1000 if tables else 0.0
    sources = prepare_sources(message.get("labrag_citations",[]))
    return {"tables":tables,"sources":sources,"content":message.get("display_content",message["content"]),
            "table_preparation_ms":round(table_preparation_ms,2),
            "preparation_ms":round((perf_counter()-preparation_started)*1000,2)}


def render_message(message, prepared=None):
    prepared = prepared if prepared is not None else prepare_message(message)
    prepared_at = perf_counter()
    tables = prepared["tables"]
    with st.chat_message(message["role"]):
        st.text(prepared["content"])
        first_content_enqueued_at = perf_counter()
        if prepared["sources"]:
            with st.expander("View sources",expanded=False):
                for source in prepared["sources"]:
                    if source["url"]:
                        st.link_button(source["label"],source["url"])
                    else:
                        st.caption(source["label"] + " (link unavailable)")
        if "overview_rows" in message:
            if message["overview_rows"]:
                st.dataframe(tables["overview_rows"],hide_index=True,width="stretch")
            else:
                st.caption("No assigned appointments match this request.")
        if "appointment_rows" in message:
            if message["appointment_rows"]:
                st.dataframe(tables["appointment_rows"],hide_index=True,width="stretch")
            else:
                st.caption("No upcoming appointments match this request.")
        if "availability_slots" in message:
            st.markdown("**Available Appointment Slots**")
            st.write(" · ".join(message["availability_slots"]) or "No available slots.")
    return {"ui_table_preparation_ms":prepared["table_preparation_ms"],
            "response_preparation_ms":prepared["preparation_ms"],
            "first_content_to_complete_enqueue_ms":round((perf_counter()-first_content_enqueued_at)*1000,2),
            "summary_and_table_enqueue_ms":round((perf_counter()-prepared_at)*1000,2)}

for message in state.get("history",[]):
    render_message(message)
render_voice(assistant,auth,st.session_state.auth_token,render_message)
render_live_voice(auth,st.session_state.auth_token)
prompt = st.chat_input("How can I help with the laboratory schedule?")
if "authenticated_at" in st.session_state:
    st.session_state.login_timing["authentication_to_workspace_ms"] = round((perf_counter()-st.session_state.pop("authenticated_at"))*1000,2)
    st.session_state.login_timing["initialization_ms"] = st.session_state.get("initialization_ms",0)
if prompt:
    # Recheck before work, including after an idle browser tab submits a request.
    if not auth.current(st.session_state.auth_token):
        clear_login()
        st.rerun()
    submitted_at = perf_counter()
    render_message({"role":"user","content":prompt})
    with st.spinner("Checking your request..."):
        state = assistant.turn(prompt,state)
        request_finished_at = perf_counter()
        prepared = prepare_message(state["history"][-1]) if state.get("history") else None
    if not state.get("history"):
        clear_login()
        st.rerun()
    st.session_state.conversation = state
    st.session_state.latest_request_channel = "text"
    render_started = perf_counter()
    render_timing = render_message(state["history"][-1],prepared)
    st.session_state.request_timing = {
        "request_ms":round((request_finished_at-submitted_at)*1000,2),
        "submit_to_result_ready_ms":round((render_started-submitted_at)*1000,2),
        "response_render_enqueue_ms":round((perf_counter()-render_started)*1000,2),
        **state.get("routing",{}).get("timing_ms",{}), **render_timing}

diagnostic_state = state
if st.session_state.get("latest_request_channel") == "voice" and st.session_state.get("voice_session"):
    diagnostic_state = st.session_state.voice_session.state
with st.sidebar.expander("Intent routing",expanded=False):
    if diagnostic_state.get("routing"):
        st.json(diagnostic_state["routing"])
    if st.session_state.get("login_timing"):
        st.caption("Login server timing (milliseconds)")
        st.json(st.session_state.login_timing)
    if st.session_state.get("request_timing") and st.session_state.get("latest_request_channel") != "voice":
        st.caption("Request server timing (milliseconds; enqueue excludes browser paint)")
        st.json(st.session_state.request_timing)
    st.markdown("**Activity · latest request**")
    names = {"match_patient":"Patient matching","patient":"Patient matching","patient_appointments":"Patient appointment retrieval",
        "overview":"Assigned schedule","staff_appointments":"Upcoming appointments","admin_overview":"Administrative overview","availability":"Staff availability",
        "prepare":"Appointment review","book":"Booking","lookup":"Appointment retrieval","information":"Laboratory information"}
    for event in diagnostic_state.get("tool_results",[]):
        st.caption(names.get(event["request"]["action"],"Request") + (" — completed" if event["result"]["ok"] else " — needs attention"))
        if event["request"]["action"] == "information":
            st.json({k:event["result"][k] for k in ("code","status","request_id","http_status","api_error","answer","citations","matched_tests") if k in event["result"]})

    labrag_messages = [message for message in diagnostic_state.get("history",[]) if message.get("labrag_activity")]
    if labrag_messages:
        st.markdown("**LabRAG request details**")
        st.json(labrag_messages[-1]["labrag_activity"])
        if labrag_messages[-1].get("labrag_citations"):
            st.json({"citations":labrag_messages[-1]["labrag_citations"]})
