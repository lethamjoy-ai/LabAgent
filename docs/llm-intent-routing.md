> Superseded by [every-turn routing](every-turn-routing.md). The historical bypasses and demo mode described below have been removed.

# LLM-first intent classification

The single Lab Assistant can use its existing Groq model integration to classify
the user's request before dispatching tools. This replaces the previous arrangement
where the model ran only after the keyword router failed, which prevented it from
correcting many misrouted questions. No second agent is introduced.

## Enable locally

Install the optional model dependency if needed:

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[dev,model]'
```

Create `.env` from `.env.example` only if it does not already exist, then set:

```dotenv
LAB_PLANNER=groq
LAB_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=your_local_router_key
LAB_ROUTER_MIN_CONFIDENCE=0.7
```

Enter the key locally, not in chat. Restart Lab Agent and start a new conversation.
`LAB_PLANNER=demo` explicitly selects the key-free offline rules. The existing API
and database configuration remain unchanged. Lab Agent does not read or copy the
neighboring LabRAG project's credentials. At implementation time, this workspace
had no `.env` and no router key in the shell, so the running app remains in offline
mode until the above configuration is supplied. Missing model configuration fails
explicitly instead of pretending the LLM is active.

## Routing contract

`ModelPlanner.classify()` makes one structured-output call returning a validated
`Plan` with an intent, confidence and optional explicitly stated request details.

| Intent | Destination |
|---|---|
| `booking` | Appointment preparation and confirmation workflow |
| `upcoming` | Future appointments assigned to the authenticated staff member |
| `overview` | Date-scoped assigned staff schedule |
| `admin_overview` | Separately authorized organization overview |
| `availability` | Staff working hours and free-slot calculation |
| `information` | LabRAG, including specimen/preparation/processing/storage queries |
| `retrieval` | Patient-specific appointments within the staff access boundary |
| `patient` / `lookup` | Patient matching / booking-reference lookup |
| `continue` | Continue the pending booking or reference clarification |
| `clarify` | Ask which operation is intended; execute no tool |

The model can recognize natural phrasing without requiring every clinical verb or
test name to match a local regex. Knowledge requests still send the **original
laboratory question unchanged** to LabRAG. Free-form test names and Bone/Serum
qualifiers remain intact; only appointment services restrict bookable test types.
Returned structured entities can fill missing booking fields, but recognizable raw
dates/times take priority, and all booking rules remain enforced by services.

Confidence below the configured threshold triggers an application-written
clarification, with no appointment or LabRAG tool executed. Confidence is the
model's self-assessment, not calibrated proof of accuracy. A malformed result,
timeout or model error likewise executes no tool. There is no silent fallback to
keyword routing in model mode. Each call uses the existing 15-second timeout,
zero automatic retries and 600-token output limit.

## Safeguards and context

Authentication is checked before classification. Model output never supplies the
acting staff identity/role, session credential or authorization. Organization-wide
access still requires the gateway's real Admin check. Staff assignment is a booking
preference, not authority to inspect someone else's schedule.

Exact raw `confirm`/`yes`/`yes confirm` and cancel turns remain application controls
and do not invoke the router. Patient name/DOB continuation is matched locally;
obvious demographic entry bypasses cloud classification. The model cannot mint
consent, identify a patient from invented fields or save an appointment directly.
Its plan contains no confirm action. An appointment is persisted only after the
existing exact-detail/session-bound confirmation and transaction checks succeed.

The classifier receives the current utterance, current lab date/timezone and minimal
context flags: current intent, whether a patient has been selected, whether patient
details/booking confirmation/laboratory clarification are pending. It receives no
conversation history, appointment records, stored DOB, staff/session identifiers
or confirmation capability. Known patient names/DOB are redacted from utterances,
as are ISO date strings and synthetic patient IDs. This is demo data minimization,
not comprehensive production PHI detection. The separate LabRAG privacy boundary
still applies to the actual laboratory question.

Each assistant message has a collapsed **Intent routing** panel showing mode,
validated intent and confidence (or application-control/failure status). The demo
configuration panel also states whether the router is Groq or offline rules.

## Testing and limitations

`tests/test_llm_intent_router.py` uses a mocked structured model and mocked LabRAG
HTTP. It verifies that known questions actually invoke the model first, schedule
and availability dispatch, verbatim laboratory questions, low-confidence/no-tool
behavior, malformed output, no silent fallback, safe context, patient entry without
a model call, raw confirmation control, Admin rejection and the UI routing panel.
Existing booking and reference regressions run alongside it.

Verification: **16 focused router/planner tests passed**, and the complete offline
suite passed **149 tests in 97.73 seconds**. The local app was reloaded and its
health endpoint returned `ok`; no live model or LabRAG query was issued.

No live model or LabRAG query was made for this change. Actual routing quality,
provider availability, latency and model-schema compatibility with a live provider
remain unverified until a local key is configured and a live check is authorized.
Routing adds one model request to each normal user turn in Groq mode. It does not
repair the independently confirmed LabRAG HTTP 503 service failure; LabRAG continues
to own retrieval, answers and its own provider dependencies.

Changed files: `app/agents/{planner,lab_assistant,state}.py`, `app/bootstrap.py`,
`app/ui/streamlit_app.py`, `.env.example`, README and new
`tests/test_llm_intent_router.py`. Appointment package/database code is unchanged.
