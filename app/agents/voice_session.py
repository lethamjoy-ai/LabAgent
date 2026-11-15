"""Session-local voice state around the existing LabRAG agent workflow."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import re
from uuid import uuid4
from .lab_assistant import LabAssistant
from .planner import Plan
from app.integrations.elevenlabs_voice import VoiceError, spoken_excerpt

TEXT_ONLY = "Voice demo supports general laboratory catalog questions only. Use text chat for appointments, confirmations, schedules, or patient records. Your text booking has not been changed."
PERSONAL_COMMAND = re.compile(r"\b(?:book|booking|schedule|schedules|scheduled|appointments?|confirm|confirmation|cancel|reserve|lookup|MRN|DOB|demographics?)\b|\b(?:patient\s+(?:records?|name|id)|date of birth|my\s+(?:results|records))\b",re.I)


class InformationOnlyGateway:
    def __init__(self, gateway):
        self.original = gateway
        # The existing privacy guard needs a local demographic deny-list.
        self.service, self.access = gateway.service, gateway.access

    def principal(self):
        return self.original.principal()

    def execute(self, request):
        # Information is executed only through the existing LabRAG boundary.
        # No appointment/patient gateway call is ever allowed from voice.
        raise PermissionError(TEXT_ONLY)


class InformationOnlyPlanner:
    def __init__(self, planner):
        self.planner, self.rejected = planner, False

    def classify(self, text, state, today, timezone):
        self.rejected = False
        decision = Plan.model_validate(self.planner.classify(text,state,today,timezone))
        allowed = decision.intent in ("information","clarify") or (
            decision.intent == "continue" and bool(state.get("labrag_original_question")))
        self.rejected = bool(PERSONAL_COMMAND.search(text) or decision.actions or not allowed)
        return Plan(intent="unsupported") if self.rejected else decision


class VoiceSession:
    def __init__(self, assistant, voice):
        self.owner = assistant
        self.voice = voice
        self.planner = InformationOnlyPlanner(assistant.planner)
        self.assistant = LabAssistant(InformationOnlyGateway(assistant.tools.gateway),assistant.labrag,
            assistant.timezone.key,planner=self.planner,now=assistant.now,
            router_min_confidence=assistant.router_min_confidence)
        self.state = {}
        self.recordings = set()
        self.failed_recordings = set()
        self.sent = set()
        self.draft_id = None
        self.transcript = ""
        self.error = None
        self.audio = {}
        self.executor = None

    def cancel(self):
        self.draft_id, self.transcript, self.error = None,"",None

    def transcribe(self, data, attempt_id=None, retry=False):
        # Deduplicate within a recorder attempt, not across the whole chat.
        # A deliberately new recording may contain identical audio/question.
        digest = (attempt_id,sha256(data).hexdigest())
        if digest in self.recordings and not (retry and digest in self.failed_recordings):
            return False
        self.recordings.add(digest)  # Failed calls are not retried on rerun.
        self.cancel()
        try:
            self.transcript = self.voice.transcribe(data)
            self.draft_id = uuid4().hex
            self.failed_recordings.discard(digest)
        except VoiceError as exc:
            self.failed_recordings.add(digest)
            self.error = str(exc)
        return True

    def submit(self, question, draft_id):
        if not draft_id or draft_id != self.draft_id or draft_id in self.sent:
            return False
        if not question.strip() or len(question) > 3000:
            self.error = "Review a short laboratory question before sending. Text chat is available."
            return False
        self.sent.add(draft_id)  # Bind Send to this reviewed recording/action.
        self.cancel()
        self.state = self.assistant.turn(question.strip(),self.state)
        if self.planner.rejected and self.state.get("history"):
            self.state["response"] = TEXT_ONLY
            self.state["history"][-1]["content"] = TEXT_ONLY
        return True

    def start_audio(self):
        if not self.state.get("history"):
            return
        message = self.state["history"][-1]
        if message.get("voice_audio_id"):
            return
        events = [event["result"] for event in self.state.get("tool_results",[])
                  if event["request"]["action"] == "information" and event["result"].get("ok")]
        if not events:
            return
        result = events[-1]
        text = spoken_excerpt(result.get("answer") or "")
        if result.get("status") == "insufficient_evidence":
            text = "The indexed references have insufficient evidence. " + text
        if result.get("status") == "answered" and not result.get("citations"):
            text = "The API returned no sources; verify with the laboratory. " + text
        identifier = uuid4().hex
        message["voice_audio_id"] = identifier
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="voice-answer")
        self.audio[identifier] = self.executor.submit(self._speak,text)

    def _speak(self, text):
        try:
            return self.voice.speak(text),None
        except Exception:
            return None,"Audio generation failed. The text answer and sources are available."

    def close(self):
        if self.executor:
            self.executor.shutdown(wait=False,cancel_futures=True)
