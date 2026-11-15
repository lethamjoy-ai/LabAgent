"""One bounded LangGraph assistant. Identity and consent stay in application code."""
import re
from time import perf_counter
from .planner import Plan
from .action_state import ActionState, PERSONAL
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
from langgraph.graph import StateGraph, START, END
from app.appointments.models import BookingDetails, GatewayRequest, AppointmentDisplay, TESTS
from app.appointments.tools import AppointmentTools
from app.appointments.presentation import appointment_text, overview_summary, overview_table, appointment_table, natural_time, natural_date
from .parsing import requested_day, requested_time, is_booking_field_reply
from .state import AssistantState
from app.staff.auth import token_hash
from app.integrations.lab_question import lab_question, LabQuestionPrivacyError
from app.integrations.labrag_client import QueryResponse, LabRAGError, HttpLabRAGClient


class LabAssistant(ActionState):
    def __init__(self, gateway, labrag, timezone="America/Los_Angeles", planner=None, now=None, router_min_confidence=0.7):
        self.tools = AppointmentTools(gateway)
        self.labrag = labrag
        self.timezone = ZoneInfo(timezone)
        self._active_action = None
        self._matched_patient = None
        self._active_confirmation = None
        self.planner = planner
        self.router_min_confidence = router_min_confidence
        clock = now or (lambda: datetime.now(self.timezone))
        self.now = lambda: clock().astimezone(self.timezone)
        graph = StateGraph(AssistantState)
        graph.add_node("plan", self.plan)
        graph.add_node("tool", self.execute)
        graph.add_node("evaluate", self.evaluate)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda s: "tool" if s.get("tool_request") else END)
        graph.add_edge("tool", "evaluate")
        graph.add_conditional_edges("evaluate", lambda s: "tool" if s.get("tool_request") and s["iterations"] < 3 else END)
        self.graph = graph.compile()

    def turn(self, message, state=None):
        turn_started = perf_counter()
        if not self.tools.gateway.principal():
            return {"history":[], "response":"Please sign in with an active staff account. Your session may have expired.",
                    "tool_results":[], "confirmation":None, "pending":None, "overview_rows":[], "overview_slots":[]}
        session = token_hash(self.tools.gateway.access.session_token)
        if state and state.get("context_session") != session:
            state = None
        current = dict(state or {})
        current["context_session"] = session
        current.update(message=message.strip(), tool_results=[], tool_request=None, iterations=0,
                       response="", overview_rows=[], overview_slots=[], appointment_rows=[], availability_slots=[], available_slots=[],routing={},booking_field_reply=False)
        current["history"] = [*current.get("history", []), {"role":"user", "content":message}]
        # Load only demographic deny-list values locally. Never include this list
        # in the router prompt or activity output.
        repository = getattr(getattr(self.tools.gateway,"service",None),"repository",None)
        if repository:
            with repository.connection() as db:
                private_values = [v for r in db.execute("SELECT name,date_of_birth FROM patients") for v in r if v]
        else:
            private_values = []
        try:
            if self.planner:
                started = perf_counter()
                try:
                    decision = Plan.model_validate(self.planner.classify(message,dict(current,private_values=private_values),self.now().date(),self.timezone.key))
                    decisions = decision.actions or [decision]
                    if any(d.actions for d in decisions):
                        raise ValueError("Nested actions are not supported")
                    # Validate the WHOLE batch before any tool or database write.
                    if len(decisions) > 1 and any(d.intent == "confirm" for d in decisions):
                        raise ValueError("Booking confirmation requires its own turn")
                    if len(decisions) > 1 and any(d.intent == "information" and not d.question for d in decisions):
                        raise ValueError("Mixed information request needs a question")
                    if any(d.confidence < self.router_min_confidence or d.intent == "clarify" for d in decisions):
                        decisions = [next(d for d in decisions if d.confidence < self.router_min_confidence or d.intent == "clarify")]
                    # Finish read-only tasks before a booking's final read-back so
                    # the visible confirmation always belongs to the pending write.
                    decisions = sorted(decisions,key=lambda d:d.intent in ("booking","continue"))
                    current["routing"] = {"mode":"LLM", "intent":decision.intent,"intents":[d.intent for d in decisions],
                        "call_count":1,"latency_ms":round((perf_counter()-started)*1000),"status":"ok"}
                except Exception as exc:
                    category = "timeout" if isinstance(exc,TimeoutError) or "timeout" in type(exc).__name__.lower() else "invalid_output" if isinstance(exc,ValueError) else "provider_failure"
                    current["routing"] = {"mode":"LLM","status":"failed","failure_category":category,"call_count":1,"latency_ms":round((perf_counter()-started)*1000)}
                    raise RuntimeError("routing failed") from exc
                result = current
                responses, events = [], []
                queued = current.get("queued_plans", []) if decision.intent == "continue" and not decision.actions and current.get("intent") == "information" else []
                queued_source = current.get("queued_action_message") or message.strip()
                sources = {}
                current["queued_plans"] = []
                current["queued_action_message"] = None
                for index, item in enumerate(decisions):
                    source = sources.get(index,message.strip())
                    step = dict(result, message=source, routed_plan=item.model_dump(mode="json"), tool_results=[],iterations=0,tool_request=None,response="")
                    if len(decisions) > 1 and item.intent == "information":
                        if not item.question:
                            raise ValueError("Mixed information request needs a question")
                        step["message"] = item.question
                    result = self.graph.invoke(step,{"recursion_limit":12})
                    responses.append(result["response"])
                    events.extend(result.get("tool_results",[]))
                    if result.get("labrag_original_question") and any(e["result"].get("status") == "needs_clarification" for e in result.get("tool_results", [])):
                        result["queued_plans"] = [d.model_dump(mode="json") for d in decisions[index+1:]] + queued
                        result["queued_action_message"] = source if decisions[index+1:] else queued_source
                        break
                    if queued and not result.get("labrag_original_question"):
                        sources.update({len(decisions)+offset:queued_source for offset in range(len(queued))})
                        decisions.extend(Plan.model_validate(p) for p in queued)
                        queued = []
                result.update(response="\n\n".join(responses),tool_results=events,message=message.strip())
                result["routing"]["tools"] = [e["request"]["action"] for e in events]
            else:
                current["routing"] = {"mode":"LLM","status":"failed","failure_category":"not_configured","call_count":0}
                raise RuntimeError("No routing LLM configured")
        except Exception:
            result = current
            self.clear_action(result)
            result.update(confirmation=None, pending=None, response="The intent classifier failed or timed out. Please retry; no tool request was submitted." if current.get("routing",{}).get("status")=="failed" else "The workflow failed. No booking success is confirmed. Please retry.")
        result.setdefault("routing",{})["timing_ms"] = {
            "server_total_ms":round((perf_counter()-turn_started)*1000,2),
            "intent_routing_ms":result.get("routing",{}).get("latency_ms",0),
            "tool_execution_ms":round(sum(e.get("execution_ms",0) for e in result.get("tool_results",[])),2),
            "database_lookup_ms":round(sum(e.get("database_lookup_ms",0) for e in result.get("tool_results",[])),2),
            "response_formatting_ms":round(sum(e.get("formatting_ms",0) for e in result.get("tool_results",[])),2),
            "table_preparation_ms":round(sum(e.get("table_ms",0) for e in result.get("tool_results",[])),2)}
        public = {"role":"assistant", "content":result["response"]}
        # Keep the complete textual response for non-UI consumers. The UI gets
        # a prepared answer and separate, lossless citations from this same turn.
        display_content = result["response"]
        citations = []
        for event in result.get("tool_results",[]):
            details = event["result"]
            if event["request"]["action"] == "information" and details.get("ok") and details.get("citations"):
                from app.integrations.labrag_client import Citation
                current_citations = details["citations"]
                suffix = "\n\nSources:\n" + "\n".join(Citation(**c).display(i) for i,c in enumerate(current_citations,1))
                display_content = display_content.replace(suffix,"",1)
                citations.extend(current_citations)
        if citations:
            public.update(display_content=display_content,labrag_citations=citations)
        if result.get("routing"):
            public["routing"] = result["routing"]
        if result.get("overview_rows") or result.get("overview_slots") or any(
                e["result"].get("code") == "overview" and e["result"]["ok"] for e in result.get("tool_results", [])):
            public.update(overview_rows=result["overview_rows"])
        if any(e["result"].get("code") == "staff_appointments" and e["result"]["ok"] for e in result.get("tool_results", [])):
            public.update(appointment_rows=result["appointment_rows"])
        if any(e["result"].get("code") == "availability" and e["result"]["ok"] for e in result.get("tool_results", [])):
            public.update(availability_slots=result["availability_slots"])
        if isinstance(self.labrag,HttpLabRAGClient):
            events = [e for e in result.get("tool_results",[]) if e["request"]["action"] == "information"]
            if events:
                details = events[-1]["result"]
                public["labrag_activity"] = {"endpoint":"POST /api/v1/query", **{
                    k:details[k] for k in ("code","status","request_id","http_status","api_error") if k in details}}
        result["history"] = [*result["history"], public]
        return result

    @staticmethod
    def clear_confirmation(s):
        s.update(confirmation=None, pending=None, confirmed=False, request_id=None)

    def plan(self, state):
        s = dict(state)
        text = s["message"]
        lower = text.lower()
        s["tool_request"] = None
        decision = None
        if self.planner:
            decision = Plan.model_validate(s["routed_plan"])
            # Every turn already crossed the LLM boundary. A field-shaped answer
            # to a saved question updates that action, even if the model calls it
            # ordinary conversation. Independent commands never match this shape.
            if (s.get("intent") == "booking" and s.get("action_id") == self._active_action
                    and self._active_action and is_booking_field_reply(text,s.get("awaiting_booking_fields"))):
                original = decision.intent
                decision = Plan(intent="continue",action_mode="update")
                s["booking_field_reply"] = True
                s["routing"] = dict(s.get("routing",{}),router_intent=original,intent="continue",continuation=True)
            if decision.confidence < self.router_min_confidence or decision.intent == "clarify":
                self.clear_action(s)
                s["response"] = decision.clarification or "Do you want to schedule an appointment, view a schedule, check available slots, or ask for laboratory test information?"
                return s
            if decision.intent in ("unsupported","conversation"):
                self.clear_action(s)
                s["response"] = "I can help with laboratory information, appointments, availability and schedules."
                return s
            route = None if decision.intent in ("continue","confirm","cancel") else decision.intent
        else:
            raise RuntimeError("A routing LLM is required")
        # Do not erase dates/times while checking consent: "yes at 12:00"
        # contains a changed detail, not approval of the current summary.
        raw = " ".join(re.sub(r"[.!?,]", "", lower).split())
        # Knowledge requests bypass appointment extraction/validation entirely.
        # A bare test name after clarification continues the full original request.
        followup = (decision.intent == "continue" and s.get("intent") == "information" and
            (s.get("labrag_original_question") or s.get("labrag_last_question")) and
            raw not in ("cancel", "no", "no cancel", "confirm", "yes", "yes confirm"))
        if route == "information" or followup:
            planned = self.information_request(s, bool(followup))
            if s.get("action_id"):
                self.clear_action(planned)
                planned["intent"] = "information"
            return planned
        if decision.intent == "cancel":
            self.clear_action(s)
            s.update(intent="", selected_slot=None, response="Pending booking cleared. Contact staff to cancel an already scheduled appointment.")
            return s
        if decision.intent == "confirm":
            if raw not in ("confirm","yes","yes confirm"):
                self.clear_action(s)
                s["response"] = "That message is not explicit approval. Please restate the patient and appointment details for a new review."
                return s
            if (not s.get("pending") or not s.get("confirmation") or not self._active_action
                    or s.get("action_id") != self._active_action or s.get("confirmation_action_id") != self._active_action
                    or s.get("confirmation") != self._active_confirmation):
                self.reset_action_fields(s)
                s["response"] = "No booking is awaiting confirmation. Please provide the patient's details and appointment preference first."
                return s
            s.update(confirmed=True, tool_request=GatewayRequest(action="book", details=BookingDetails(**s["pending"]),
                request_id=s["request_id"], confirmation_token=s["confirmation"]).model_dump(mode="json"))
            return s

        if decision.intent in PERSONAL or (decision.intent == "continue" and s.get("intent") in PERSONAL):
            return self.personal_plan(s,decision)
        if s.get("action_id"):
            self.clear_action(s)
        routed = route
        previous_intent = s.get("intent")
        if routed:
            s["labrag_original_question"] = None
            s["labrag_clarifications"] = []
            s["intent"] = routed
            if routed in ("upcoming", "overview", "admin_overview", "availability", "information"):
                s["awaiting_identity"] = False
            if routed != previous_intent:
                self.clear_confirmation(s)
                s.update(requested_date=None, selected_slot=None, lookup_id=None, appointment_filter="all")
        intent = s.get("intent", "")
        if intent in ("retrieval", "upcoming", "overview", "admin_overview"):
            if routed in ("retrieval", "upcoming", "overview", "admin_overview"):
                s["requested_date"] = None
            if "upcoming" in lower:
                s["appointment_filter"] = "upcoming"
            elif "completed" in lower:
                s["appointment_filter"] = "completed"
            elif "historical" in lower or "past" in lower:
                s["appointment_filter"] = "historical"
            elif routed in ("retrieval", "upcoming", "overview", "admin_overview"):
                s["appointment_filter"] = "all"

        old = tuple(s.get(k) for k in ("patient", "test", "requested_date", "selected_slot", "target_staff_name"))
        target = re.search(r"\bwith\s+([A-Za-z][A-Za-z'-]*(?:\s+(?!today\b|tomorrow\b|on\b|at\b|for\b)[A-Za-z][A-Za-z'-]*)?)(?=\s+(?:today|tomorrow|on|at|for)\b|[,?.]|$)", text, re.I)
        if intent in ("upcoming", "overview", "admin_overview"):
            target = (re.search(r"\b([A-Za-z]+(?:\s+[A-Za-z]+)?)'s\s+(?:upcoming\s+)?(?:schedule|appointments)",text,re.I)
                or re.search(r"\bschedule\s+for\s+([A-Za-z]+)(?:\s|$)",text,re.I))
            if routed:
                s["target_staff_name"] = None
        if target:
            s["target_staff_name"] = re.sub(r"^(?:show|view|check)\s+", "", target.group(1), flags=re.I).strip()
        elif decision and decision.staff_name:
            s["target_staff_name"] = decision.staff_name
        try:
            day = requested_day(text, self.now().date())
            slot = requested_time(text)
        except ValueError as exc:
            self.clear_confirmation(s)
            s["response"] = str(exc)
            if "Which date" in str(exc):
                s["requested_date"] = None
            if "Which time" in str(exc):
                s["selected_slot"] = None
            return s
        if day:
            s["requested_date"] = day.isoformat()
        if slot:
            s["selected_slot"] = slot
        if decision:
            if not day and decision.day:
                s["requested_date"] = decision.day.isoformat()
            if not slot and decision.time:
                s["selected_slot"] = decision.time
        catalog = TESTS
        candidates = [key for key, name in catalog.items()
            if re.search(r"\b"+key+r"\b", text, re.I) or name.lower() in lower]
        if "glucose" in lower and "GLU" not in candidates:
            candidates.append("GLU")
        if len(candidates) > 1 or ("blood" in lower and not candidates):
            self.clear_confirmation(s)
            s.update(test=None, response="Which test? CBC - Complete blood count; GLU - Glucose; A1C - Hemoglobin A1c.")
            return s
        if candidates:
            s["test"] = candidates[0]
        elif decision and decision.test_id:
            s["test"] = decision.test_id
        if decision and decision.appointment_filter != "all":
            s["appointment_filter"] = decision.appointment_filter
        found = re.search(r"\b(?:LAB-[a-f0-9]+|DEMO-\d{4}-\d{2}-\d{2}-\d+)\b", text, re.I)
        if found:
            s["lookup_id"] = found.group()
        elif decision and decision.appointment_id:
            s["lookup_id"] = decision.appointment_id
        new = tuple(s.get(k) for k in ("patient", "test", "requested_date", "selected_slot", "target_staff_name"))
        if old != new:
            self.clear_confirmation(s)
        return self.next_request(s)

    def booking_context(self, s):
        if s.get("intent") != "booking" or not s.get("requested_date"):
            return ""
        day = datetime.fromisoformat(s["requested_date"])
        when = natural_date(day) + f" ({s['requested_date']}, {self.timezone.key})"
        if s.get("selected_slot"):
            when += " at " + natural_time(datetime.fromisoformat(s["requested_date"]+"T"+s["selected_slot"]))
        return f"Requested appointment: {TESTS.get(s.get('test'), 'laboratory test')} on {when}. "

    def information_request(self, s, followup=False):
        if not followup:
            self.clear_confirmation(s)
            s.update(labrag_original_question=s["message"], labrag_last_question=s["message"],
                labrag_clarifications=[], labrag_clarification_count=0, labrag_last_clarification=None)
        original = s.get("labrag_original_question") or s.get("labrag_last_question") or s["message"]
        replies = list(s.get("labrag_clarifications", []))
        if followup and s["message"].casefold() not in [r.casefold() for r in replies]:
            replies.append(s["message"])
        try:
            repository = getattr(getattr(self.tools.gateway,"service",None),"repository",None)
            with repository.connection() as db:
                private_values = [v for r in db.execute("SELECT name,date_of_birth FROM patients") for v in r if v]
            question = lab_question(original, clarification="\n".join(replies),
                sensitive_values=(*private_values,s.get("patient_name"),s.get("date_of_birth")))
        except LabQuestionPrivacyError as exc:
            s.update(response=str(exc), tool_request=None)
            return s
        if followup and question == s.get("labrag_last_query"):
            s.update(response="Those exact details have already been sent to LabRAG. Please provide a different catalog test code, provider, or exact catalog title, or check the source catalog with laboratory staff.",tool_request=None)
            return s
        s.update(intent="information", awaiting_identity=False, labrag_clarifications=replies,
            labrag_original_question=original, labrag_last_query=question,
            tool_request={"action":"information", "test_id":None, "question":question})
        return s

    def next_request(self, s):
        intent = s.get("intent")
        if intent == "upcoming":
            request = GatewayRequest(action="staff_appointments", day=s.get("requested_date"),
                target_staff_name=s.get("target_staff_name"), appointment_filter="upcoming")
        elif intent in ("overview", "admin_overview"):
            day = s.get("requested_date") or self.now().date().isoformat()
            request = GatewayRequest(action=intent, day=day, test_id=s.get("test") if s.get("test") in TESTS else "CBC",
                target_staff_name=s.get("target_staff_name"), appointment_filter=s.get("appointment_filter", "all"))
        elif intent == "availability":
            if not s.get("requested_date"):
                s["response"] = "Please provide a date to check staff availability."
                return s
            request = GatewayRequest(action="availability", day=s["requested_date"], test_id=s.get("test") if s.get("test") in TESTS else "CBC", target_staff_name=s.get("target_staff_name"))
        elif intent == "information":
            return self.information_request(s)
        elif intent == "retrieval":
            request = GatewayRequest(action="patient_appointments", patient_id=s["patient"],
                day=s.get("requested_date"), appointment_filter=s.get("appointment_filter", "all"))
        elif intent == "lookup":
            if not s.get("lookup_id"):
                s["response"] = "Please provide the patient's booking reference, or ask to show your assigned schedule."
                return s
            request = GatewayRequest(action="lookup", appointment_id=s["lookup_id"], patient_id=s["patient"])
        elif intent == "patient":
            request = GatewayRequest(action="patient", patient_id=s["patient"])
        elif intent == "booking":
            missing = [label for key,label in [("test", "laboratory test (CBC, GLU or A1C)"),
                ("requested_date", "appointment date"), ("selected_slot", "appointment time")] if not s.get(key)]
            if missing:
                s["awaiting_booking_fields"] = [field for field,key in (("test","test"),("date","requested_date"),("time","selected_slot")) if not s.get(key)]
                s["response"] = "Please provide " + ", ".join(missing) + "."
                return s
            details = BookingDetails(patient_id=s["patient"], test_id=s["test"],
                start=datetime.fromisoformat(s["requested_date"]+"T"+s["selected_slot"]).replace(tzinfo=self.timezone))
            s.update(pending=details.model_dump(mode="json"), request_id=s.get("action_id") or uuid4().hex, confirmed=False)
            s["awaiting_booking_fields"] = []
            request = GatewayRequest(action="prepare", details=details, request_id=s["request_id"], target_staff_name=s.get("target_staff_name"))
        else:
            s["response"] = "I can schedule patient appointments, show your assigned schedule, check staff availability, or answer laboratory test questions."
            return s
        s["tool_request"] = request.model_dump(mode="json")
        return s

    def execute(self, state):
        execution_started = perf_counter()
        database_lookup_ms = 0.0
        request = state["tool_request"]
        if request["action"] == "information":
            try:
                answer = self.labrag.query(request["test_id"], request["question"])
                result = {"ok":True, "code":"information", "message":answer.display()}
                if isinstance(answer,QueryResponse):
                    result.update(status=answer.status, answer=answer.answer, citations=[c.model_dump() for c in answer.citations],
                        matched_tests=[t.model_dump() for t in answer.matched_tests], request_id=answer.request_id)
            except LabRAGError as exc:
                result = {"ok":False, "code":exc.code, "message":str(exc), "http_status":exc.http_status,
                    "request_id":exc.request_id,"api_error":exc.api_error}
            except Exception:
                result = {"ok":False, "code":"labrag_error", "message":"LabRAG failed or timed out. No specimen instructions could be verified. Try again later."}
        else:
            personal = request["action"] in ("match_patient","patient","patient_appointments","lookup","prepare","book")
            if personal and not self.personal_tool_allowed(state,request):
                result = {"ok":False,"code":"identity_required","message":"Start this patient action with first name, last name and date of birth."}
            elif request["action"] == "book" and (request.get("confirmation_token") != self._active_confirmation or state.get("confirmation_action_id") != self._active_action):
                result = {"ok":False,"code":"confirmation_required","message":"Review the current action details and confirm again."}
            else:
                database_started = perf_counter()
                result = self.tools.invoke(GatewayRequest(**request)).model_dump(mode="json")
                if request["action"] in ("overview","admin_overview","staff_appointments"):
                    database_lookup_ms = (perf_counter()-database_started)*1000
        return {"tool_results":[*state.get("tool_results", []), {"request":request, "result":result,
                "execution_ms":(perf_counter()-execution_started)*1000,"database_lookup_ms":database_lookup_ms}],
                "iterations":state["iterations"]+1, "tool_request":None}

    def evaluate(self, state):
        formatting_started = perf_counter()
        event = state["tool_results"][-1]
        result, request = event["result"], event["request"]
        code = result["code"]
        update = {"tool_request":None}
        rows = [AppointmentDisplay(**r) for r in result.get("display_rows", [])]
        if code == "information" and result["ok"]:
            update.update(response=result["message"], labrag_original_question=(state.get("labrag_original_question") or state["message"])
                if result.get("status") == "needs_clarification" else None)
            if result.get("status") != "needs_clarification":
                update["labrag_clarifications"] = []
            else:
                count = state.get("labrag_clarification_count",0)+1
                clarification = result.get("answer",result["message"])
                repeated = clarification.strip().casefold() == str(state.get("labrag_last_clarification") or "").strip().casefold()
                update.update(labrag_clarification_count=count,labrag_last_clarification=clarification)
                if state.get("labrag_clarifications") and (repeated or count >= 3):
                    names = [t.get("test_name") or t.get("test_code") for t in result.get("matched_tests",[]) if t.get("test_name") or t.get("test_code")]
                    update.update(labrag_original_question=None,
                        response="LabRAG still could not resolve the supplied test name from its indexed references. "
                        "Verify the exact catalog test code, provider and specimen qualifier, or check the source catalog with laboratory staff. "
                        "I will not substitute a different test." + (" Returned matches: "+"; ".join(names) if names else ""))
                    if result.get("citations"):
                        from app.integrations.labrag_client import Citation
                        update["response"] += "\n\nSources:\n" + "\n".join(Citation(**c).display(i) for i,c in enumerate(result["citations"],1))
        elif code == "patient" and result["ok"]:
            p = result["patient"]
            self._matched_patient = p["id"]
            update.update(patient=p["id"], patient_name=p["name"], awaiting_identity=False,matched_action_id=state.get("action_id"))
            if request["action"] == "match_patient" and state.get("intent") != "patient":
                continuation = dict(state)
                continuation.update(update)
                update = self.next_request(continuation)
            else:
                update["response"] = f"I found {p['name']}'s patient record."
        elif code == "confirmation_required" and result["ok"]:
            self._active_confirmation = result["confirmation_token"]
            update.update(confirmation=result["confirmation_token"], confirmation_action_id=state.get("action_id"), response=result["message"], pending=result["selected_details"])
        elif code == "booked" and result["ok"]:
            self.clear_action(update)
            update.update(response="The appointment has been successfully scheduled. " +
                (appointment_text(rows[0], self.timezone, self.now()) if rows else ""), confirmed=False)
        elif code == "unavailable":
            update.update(confirmation=None, pending=None, selected_slot=None, request_id=None,
                awaiting_booking_fields=["time"], available_slots=result.get("slots", []), response=result["message"]+
                (" Available alternatives: "+self.slot_text(result["slots"]) if result.get("slots") else ""))
        elif code == "overview" and result["ok"]:
            day = request["day"]
            scope = "organization-wide" if request["action"] == "admin_overview" else "your assigned"
            table_started = perf_counter()
            table_rows = overview_table(rows, self.timezone)
            event["table_ms"] = (perf_counter()-table_started)*1000
            update.update(overview_rows=table_rows,
                overview_slots=[natural_time(datetime.fromisoformat(s["start"]).astimezone(self.timezone)) + " - " + s["staff_name"] for s in result["slots"]],
                available_slots=result["slots"], response=f"Here is {scope} laboratory appointment summary for {day}. " + overview_summary(rows))
        elif code == "availability" and result["ok"]:
            update.update(available_slots=result["slots"],
                availability_slots=[natural_time(datetime.fromisoformat(s["start"]).astimezone(self.timezone)) + " - " + s["staff_name"] for s in result["slots"]],
                response=result.get("message") or f"Available appointment slots for {request['day']} ({self.timezone.key}).")
        elif code == "staff_appointments" and result["ok"]:
            count = len(rows)
            update.update(appointment_rows=appointment_table(rows,self.timezone),
                response=f"You have {count} upcoming scheduled laboratory appointment{'s' if count != 1 else ''}" +
                (f" on {request['day']}" if request.get("day") else "") + f" ({self.timezone.key}).")
        elif code in ("patient_appointments", "lookup") and result["ok"]:
            update["response"] = (f"I found {len(rows)} laboratory appointment{'s' if len(rows) != 1 else ''}.\n" +
                "\n".join(appointment_text(r, self.timezone, self.now()) for r in rows)) if rows else "No appointments match your request."
        else:
            update["response"] = result.get("message") or "No appointments match your request."
            if code in ("identity_not_found", "identity_not_unique", "identity_required", "access_denied"):
                update.update(patient=None, confirmation=None, pending=None)
                if request["action"] == "match_patient":
                    update.update(awaiting_identity=True, date_of_birth=None)
        if result["ok"] and code in ("patient_appointments","lookup") or (result["ok"] and code == "patient" and state.get("intent") == "patient"):
            self.clear_action(update)
        event["formatting_ms"] = (perf_counter()-formatting_started)*1000
        return update

    def slot_text(self, slots):
        return "; ".join(natural_time(datetime.fromisoformat(s["start"]).astimezone(self.timezone)) +
            (f" with {s['staff_name']}" if s.get("staff_name") else "") for s in slots) or "No available slots."
