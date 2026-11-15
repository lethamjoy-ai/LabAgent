"""Local identity and consent scope for one patient-specific action."""
import re
from uuid import uuid4
from .parsing import (extract_identity, name_component_matches,
                      appointment_text_without_dob, requested_day, requested_time, DOB_PATTERN)
from app.appointments.models import GatewayRequest, TESTS
from app.appointments.repository import normalize_name

PERSONAL = {"booking", "retrieval", "patient", "lookup"}


class ActionState:
    def clear_action(self, s):
        self._active_action = None
        self._matched_patient = None
        self._active_confirmation = None
        self.reset_action_fields(s)

    @staticmethod
    def reset_action_fields(s):
        s.update(action_id=None, matched_action_id=None, confirmation_action_id=None,
                 patient=None, patient_name=None, first_name=None, last_name=None,
                 date_of_birth=None, test=None, requested_date=None, selected_slot=None,
                 requested_time=None,
                 target_staff_name=None, lookup_id=None, awaiting_identity=False,
                 appointment_filter="all",
                 available_slots=[], confirmation=None, pending=None, confirmed=False,
                 request_id=None, intent="", pending_action_message=None,
                 awaiting_booking_fields=[], booking_field_reply=False)

    def begin_action(self, s, intent):
        self.clear_action(s)
        self._active_action = uuid4().hex
        s.update(action_id=self._active_action, intent=intent, awaiting_identity=True)

    def invalidate_confirmation(self, s):
        self._active_confirmation = None
        self.clear_confirmation(s)

    def identity_question(self, s):
        missing = [label for key, label in (("first_name","first name"),
                   ("last_name","last name"),("date_of_birth","date of birth")) if not s.get(key)]
        if len(missing) == 3:
            question = "What is the patient's first name, last name, and date of birth?"
        else:
            question = "Please provide the patient's " + " and ".join(missing) + "."
        return self.booking_context(s) + question

    def personal_plan(self, s, decision):
        text = s["message"]
        intent = decision.intent if decision.intent in PERSONAL else s.get("intent")
        if s.get("pending_action_message") and decision.action_mode in ("new","update"):
            text = s["pending_action_message"]
            s["pending_action_message"] = None
            s["message"] = text
        active = bool(s.get("action_id") and s["action_id"] == self._active_action)
        # Parse identity from this utterance only, never from model arguments.
        explicit = bool(re.search(r"\b(?:first[ -]+name|last[ -]+name|dob|date of birth|born|name:)\b",text,re.I))
        identity_reply = (active and s.get("awaiting_identity") and not decision.fresh_booking
                          and not s.get("booking_field_reply"))
        identity_error = None
        try:
            name, dob = extract_identity(text, awaiting=bool(identity_reply))
        except ValueError as exc:
            name, dob = None, None
            identity_error = str(exc)
            self.invalidate_confirmation(s)
        parts = []
        for label in (r"first[ -]+name",r"last[ -]+name"):
            matches = name_component_matches(text,label)
            values = {normalize_name(m.group(1).strip(" .")) for m in matches}
            parts.append(" ".join(matches[0].group(1).strip(" .").split()) if len(values) == 1 else None)
        if name and not any(parts):
            words = name.split()
            parts = [words[0], " ".join(words[1:])]
        switch = active and any(
            value and s.get(key) and str(value).casefold() != str(s[key]).casefold()
            for key,value in (("first_name",parts[0]),("last_name",parts[1]),("date_of_birth",dob.isoformat() if dob else None)))
        new = (not active or intent != s.get("intent") or decision.fresh_booking
               or decision.action_mode == "new" or switch or (identity_error and explicit))
        if (active and not new and decision.intent in PERSONAL and decision.action_mode != "update"
                and not (identity_reply and (any(parts) or dob or explicit))):
            self.invalidate_confirmation(s)
            s.update(pending_action_message=text,
                     response="Is this a correction to the unfinished action or a new patient action? Please specify which before proceeding.")
            return s
        if decision.action_mode == "ambiguous":
            self.invalidate_confirmation(s)
            s.update(pending_action_message=text,response="Is this a correction to the unfinished action or a new patient action?")
            return s
        if new:
            self.begin_action(s,intent)
        if intent not in PERSONAL:
            s["response"] = "Please specify the new appointment or patient-record action and its patient details."
            return s
        previous = tuple(s.get(k) for k in ("test","requested_date","selected_slot","target_staff_name"))
        errors = [identity_error] if identity_error else []
        awaiting = []
        if any(parts) or dob:
            self.invalidate_confirmation(s)
            s.update(patient=None,matched_action_id=None)
            self._matched_patient = None
            for key,value in (("first_name",parts[0]),("last_name",parts[1]),("date_of_birth",dob.isoformat() if dob else None)):
                if value:
                    s[key] = value
        s["patient_name"] = " ".join((s.get("first_name") or "",s.get("last_name") or "")).strip() or None
        try:
            clean = appointment_text_without_dob(text,awaiting=bool(identity_reply))
        except ValueError:
            clean = re.sub(DOB_PATTERN," ",text,flags=re.I)
        # Each valid supplied field is merged even if another needs clarification.
        try:
            day = requested_day(clean,self.now().date())
            if day:
                if day.isoformat() != s.get("requested_date"):
                    s["selected_slot"] = s.get("requested_time")
                s["requested_date"] = day.isoformat()
        except ValueError as exc:
            s["requested_date"] = None
            awaiting.append("date")
            errors.append(str(exc))
        try:
            slot = requested_time(clean)
            if slot:
                s["selected_slot"] = s["requested_time"] = slot
        except ValueError as exc:
            s["selected_slot"] = s["requested_time"] = None
            awaiting.append("time")
            errors.append(str(exc))
        tests = [key for key,value in TESTS.items() if re.search(r"\b"+key+r"\b",clean,re.I) or value.casefold() in clean.casefold()]
        if "glucose" in clean.casefold() and "GLU" not in tests:
            tests.append("GLU")
        if len(tests) > 1 or ("blood" in clean.casefold() and not tests):
            s["test"] = None
            awaiting.append("test")
            errors.append("Which test? CBC, GLU or A1C.")
        elif tests:
            s["test"] = tests[0]
        # Appointment entities must also be grounded in this action's raw input.
        # Router context can contain an older slot; it is never a source of values.
        target = re.search(r"\bwith\s+([A-Za-z][A-Za-z'-]*(?:\s+(?!today\b|tomorrow\b|on\b|at\b|for\b)[A-Za-z][A-Za-z'-]*)?)(?=\s+(?:today|tomorrow|on|at|for)\b|[,?.;]|$)",clean,re.I)
        if target:
            s["target_staff_name"] = target.group(1)
        reference = re.search(r"\b(?:LAB-[a-f0-9]+|DEMO-\d{4}-\d{2}-\d{2}-\d+)\b",text,re.I)
        if reference:
            s["lookup_id"] = reference.group()
        if decision.intent != "continue":
            s["appointment_filter"] = decision.appointment_filter
        if previous != tuple(s.get(k) for k in ("test","requested_date","selected_slot","target_staff_name")):
            self.invalidate_confirmation(s)
        s["awaiting_identity"] = not all(s.get(k) for k in ("first_name","last_name","date_of_birth"))
        s["awaiting_booking_fields"] = list(dict.fromkeys(awaiting + [
            field for field,key in (("test","test"),("date","requested_date"),("time","selected_slot")) if not s.get(key)]))
        if errors:
            s["response"] = " ".join(errors)
            return s
        if not all(s.get(k) for k in ("first_name","last_name","date_of_birth")):
            s.update(awaiting_identity=True,awaiting_booking_fields=[],response=self.identity_question(s))
            return s
        if not self._matched_patient or s.get("matched_action_id") != s["action_id"]:
            s["tool_request"] = GatewayRequest(action="match_patient",patient_name=s["patient_name"],date_of_birth=s["date_of_birth"]).model_dump(mode="json")
            return s
        return self.next_request(s)

    def personal_tool_allowed(self, s, request):
        if not self._active_action or s.get("action_id") != self._active_action:
            return False
        if request["action"] == "match_patient":
            return (all(s.get(k) for k in ("first_name","last_name","date_of_birth"))
                    and normalize_name(request.get("patient_name") or "") == normalize_name(s.get("patient_name") or "")
                    and request.get("date_of_birth") == s.get("date_of_birth"))
        patient = (request.get("details") or {}).get("patient_id") or request.get("patient_id")
        return (bool(self._matched_patient) and s.get("matched_action_id") == self._active_action
                and patient == s.get("patient") == self._matched_patient)
