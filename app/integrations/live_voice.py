"""Server-side, one-use session minting; never log a signed URL or API key."""
import os
from urllib.parse import urlsplit
from uuid import UUID
from .elevenlabs_client import create_elevenlabs_client
from dotenv import load_dotenv
from pathlib import Path


class LiveSetupError(ValueError):
    pass


def require_readonly_agent(client,agent_id):
    config = client.conversational_ai.agents.get(agent_id=agent_id,
        request_options={"max_retries":0}).model_dump()
    prompt = config.get("conversation_config",{}).get("agent",{}).get("prompt",{})
    tools = list(prompt.get("tools") or [])
    identifiers = prompt.get("tool_ids") or []
    if len(identifiers)>8:
        raise LiveSetupError("Configure this agent with lookup_lab_test, optional get_appointment_details/create_appointment/get_daily_overview and end_call before starting live voice.")
    for identifier in identifiers:
        tool = client.conversational_ai.tools.get(tool_id=identifier,
            request_options={"max_retries":0}).model_dump()
        tools.append(tool.get("tool_config",tool))
    names = {tool.get("name") for tool in tools if isinstance(tool,dict)}
    if "lookup_lab_test" not in names or names-{"lookup_lab_test","get_appointment_details","create_appointment","get_daily_overview","end_call"}:
        raise LiveSetupError("Complete the dashboard setup: attach lookup_lab_test and permit only get_appointment_details, create_appointment, get_daily_overview and optional end_call alongside it. See docs/live-voice.md.")
    for tool in tools:
        if tool.get("name") in {"lookup_lab_test","get_appointment_details","create_appointment","get_daily_overview"}:
            schema=tool.get("api_schema") or {}
            target=urlsplit(schema.get("url") or "")
            if (tool.get("type")!="webhook" or schema.get("method","").upper()!="POST"
                    or target.scheme!="https" or target.path.rstrip('/')!="/api/v1/tools/"+tool["name"]):
                raise LiveSetupError("Configure each approved tool as a POST Webhook at its matching public HTTPS /api/v1/tools/<tool_name> route.")
            if tool.get("name")=="create_appointment":
                parameter=(schema.get("request_body_schema") or {}).get("properties",{}).get("confirmed")
                if parameter and parameter.get("type")!="boolean":
                    raise LiveSetupError("Set create_appointment's confirmed body parameter to Boolean, not String. Send true only after explicit consent.")
            if tool.get("name")=="lookup_lab_test":
                timeout=tool.get("response_timeout_secs")
                if timeout is not None and timeout < float(os.getenv("LABRAG_TIMEOUT_SECONDS","90"))+10:
                    raise LiveSetupError("Increase lookup_lab_test's response timeout to cover LABRAG_TIMEOUT_SECONDS plus network overhead (120 seconds for the default settings).")
    if not config.get("platform_settings",{}).get("auth",{}).get("enable_auth"):
        raise LiveSetupError("Enable authentication in the agent's Security tab before starting live voice.")


def live_configuration_present():
    load_dotenv(Path(__file__).resolve().parents[2]/".env")
    return bool(os.getenv("ELEVENLABS_AGENT_ID") and os.getenv("ELEVENLABS_API_KEY"))


def signed_conversation_url(client=None):
    if not live_configuration_present():
        raise ValueError("Configure ELEVENLABS_AGENT_ID and ELEVENLABS_API_KEY on the server.")
    try:
        client = client or create_elevenlabs_client()
        require_readonly_agent(client,os.environ["ELEVENLABS_AGENT_ID"])
        response = client.conversational_ai.conversations.get_signed_url(
            agent_id=os.environ["ELEVENLABS_AGENT_ID"],include_conversation_id=True,
            request_options={"max_retries":0})
        parsed = urlsplit(response.signed_url)
        if parsed.scheme!="wss" or not (parsed.hostname or "").endswith(".elevenlabs.io") or not parsed.path.startswith("/v1/convai/conversation"):
            raise ValueError("Invalid signed URL")
        return response.signed_url
    except LiveSetupError:
        raise
    except Exception:
        raise ValueError("Live voice could not connect. Try Start again explicitly, or use text/reviewed voice.") from None


def valid_start_event(event):
    if not isinstance(event,dict) or event.get("action")!="start":
        return None
    try:
        return str(UUID(event.get("request_id","")))
    except (ValueError,TypeError,AttributeError):
        return None
