import os
from datetime import datetime
from dotenv import load_dotenv
from app.appointments.repository import Repository
from app.appointments.service import AppointmentService
from app.appointments.gateway import DirectAppointmentGateway
from app.appointments.access import AccessContext
from app.staff.auth import AuthService
from app.agents.lab_assistant import LabAssistant
from app.integrations.labrag_client import HttpLabRAGClient


def build_labrag_client():
    return HttpLabRAGClient(os.getenv("LABRAG_BASE_URL", "http://127.0.0.1:8000"),
        float(os.getenv("LABRAG_TIMEOUT_SECONDS", "90")))


def create_auth():
    load_dotenv()
    return AuthService(Repository(os.getenv("LAB_DATABASE", "data/labassist.sqlite3")),
        ttl_seconds=int(os.getenv("LAB_SESSION_SECONDS", "1800")))


def create_assistant(access: AccessContext | None = None, provider: str | None = None):
    load_dotenv()
    service = AppointmentService(Repository(os.getenv("LAB_DATABASE", "data/labassist.sqlite3")),
        os.getenv("LAB_TIMEZONE", "America/Los_Angeles"))
    service.repository.seed(datetime.now(service.timezone).date(), service.timezone)
    client = build_labrag_client()
    configured_provider = os.getenv("LAB_PLANNER", "openai")
    planner_mode = provider or configured_provider
    planner = None
    if planner_mode == "openai":
        from app.agents.planner import openai_planner
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("Set OPENAI_API_KEY for the routing LLM. No offline fallback is enabled.")
        model_name = os.getenv("OPENAI_MODEL") or (os.getenv("LAB_MODEL") if configured_provider == planner_mode else None) or "gpt-4.1-mini"
        planner = openai_planner(model_name, key)
    elif planner_mode == "groq":
        from app.agents.planner import groq_planner
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            raise ValueError("Set GROQ_API_KEY for the required routing LLM. No offline fallback is enabled.")
        model_name = os.getenv("GROQ_MODEL") or (os.getenv("LAB_MODEL") if configured_provider == planner_mode else None) or "llama-3.3-70b-versatile"
        planner = groq_planner(model_name, key)
    else:
        raise ValueError("LAB_PLANNER must be openai or groq. Offline tests must inject a mocked LLM router.")
    threshold=float(os.getenv("LAB_ROUTER_MIN_CONFIDENCE", "0.7"))
    if not 0 <= threshold <= 1:
        raise ValueError("LAB_ROUTER_MIN_CONFIDENCE must be between 0 and 1.")
    assistant = LabAssistant(DirectAppointmentGateway(service, access), client, service.timezone.key, planner=planner, now=service.now,router_min_confidence=threshold)
    assistant.router_provider = planner_mode
    assistant.router_model = model_name
    return assistant
