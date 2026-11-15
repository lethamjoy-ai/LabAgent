"""Offline routing double. Rules exist ONLY in tests, never runtime fallback."""
import re
from app.agents.planner import Plan
from app.agents.lab_assistant import LabAssistant
from app.agents.parsing import requested_day, requested_time
from app.appointments.models import TESTS

class OfflineRouter:
    @staticmethod
    def route(text):
        lower = text.lower()
        if re.search(r"\b(?:specimens?|containers?|preparation|information|info|volumes?|fasting|stability|handling|transport|turnaround)\b", lower):
            return "information"
        # General patient instructions are reference questions, not patient lookup.
        # Recognize verb forms as well as nouns (prepare vs preparation).
        if (re.match(r"^(?:how|what|when|should|does|do|is|are|can|could)\b",lower) and
                re.search(r"\b(?:prepare|prepares|prepared|preparing|collect|collected|collecting|collection|fast|eat|drink)\b",lower)):
            return "information"
        if any(w in lower for w in ("all schedules", "organization", "all staff", "administrative overview")):
            return "admin_overview"
        if re.search(r"\b(?:availability|available|slots)\b", lower):
            return "availability"
        if "upcoming" in lower and ("appointment" in lower or "schedule" in lower) and "patient" not in lower:
            return "upcoming"
        if (re.search(r"\bmy\s+(?:\w+\s+)?(?:schedule|appointments)\b",lower) or
                "appointments do i have" in lower or "how many patients" in lower or
                re.search(r"\b\w+(?:\s+\w+)?'s\s+(?:schedule|appointments)\b",lower)):
            return "overview"
        if "overview" in lower or "daily" in lower or "laboratory schedule" in lower:
            return "overview"
        if re.search(r"\b(?:book|schedule|reserve)\b", lower):
            return "booking"
        if "lookup" in lower or "appointment id" in lower:
            return "lookup"
        if ("appointment" in lower or "tests" in lower) and re.search(
                r"\b(?:show|what|have|upcoming|completed|status|check|scheduled|past|historical)\b", lower):
            return "retrieval"
        if "patient" in lower and re.search(r"\b(?:find|lookup|look up|identify|search|record|demographic|dob|date of birth|name|address)\b",lower):
            return "patient"
        if re.fullmatch(r"(?:can|could) you (?:assist|help)(?: me)?(?: please)?[?.!]*",lower):
            return None
        # Once explicit scheduling/record intents are excluded, natural reference
        # questions belong to LabRAG. Do not require every processing/storage verb
        # or test abbreviation to be enumerated in this router.
        if (re.match(r"^(?:how|what|when|why|which|should|can|could|do|does|is|are)\b",lower)
                and not re.search(r"\b(?:finalize|confirm|cancel|proceed)\b",lower)):
            return "information"
        if "patient" in lower:
            return "patient"
        if re.match(r"^appointment\b", lower):
            return "booking"
        return None

    def classify(self,text,state,today,timezone):
        raw=re.sub(r"[^a-z\s]","",text.lower()).strip()
        if raw in ("yes","confirm","yes confirm"):
            return Plan(intent="confirm")
        if raw in ("no","cancel","no cancel"):
            return Plan(intent="cancel")
        if any(x in raw for x in ("you got it wrong","you misunderstood","not what i asked","thats wrong")):
            return Plan(intent="clarify",clarification="Please restate the laboratory question or specify which answer detail needs correction.")
        route=self.route(text)
        if state.get("intent")=="information" and state.get("labrag_original_question") and not re.match(r"^(what|how|show|schedule|book)\b",text,re.I):
            route="continue"
        if state.get("awaiting_identity") and not route:
            return Plan(intent="continue")
        return Plan(intent=route or "continue",appointment_filter="upcoming" if "upcoming" in text.lower() else "completed" if "completed" in text.lower() else "historical" if "past" in text.lower() or "historical" in text.lower() else "all",fresh_booking=route=="booking" and bool(re.search(r"\b(?:book|schedule|reserve)\b",text,re.I)))
