# LabAssist

Week 3 laboratory staff assistant: one LangGraph workflow, SQLite appointments,
and a Streamlit text interface. All patients and appointment seeds are synthetic.

**Current workflow: authenticated laboratory staff, not patients.** Each account has
its own schedule and configurable working hours. Read the current
[multi-staff setup and implementation report](docs/multi-staff-upgrade.md) for login,
secure password setup, the migration backup, demo instructions and security limits.
That guide supersedes the earlier patient-mode/demo-token walkthrough below.

## Run

Python 3.11+ is required. In PowerShell from this directory:

```powershell
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -e '.[dev,model]'
Copy-Item .env.example .env  # only if .env does not exist
.venv/Scripts/python.exe -m scripts.setup_staff  # hidden local password prompts
.venv/Scripts/python.exe -m streamlit run streamlit_app.py
.venv/Scripts/python.exe -m pytest -q
```

Alternatively use `python -m venv .venv` and `.venv/Scripts/python.exe -m pip install -e '.[dev,model]'`.
On macOS/Linux use `.venv/bin/python`. Configure `LAB_PLANNER=openai`,
`OPENAI_API_KEY`, and `OPENAI_MODEL` in your local `.env` before signing in.
Use the **Routing LLM provider** selector in the left sidebar to switch between
OpenAI and Groq. Provider changes retain conversation state and do not resubmit a
message. OpenAI uses `OPENAI_API_KEY` and `OPENAI_MODEL` (default `gpt-4.1-mini`);
Groq uses `GROQ_API_KEY` and `GROQ_MODEL` (default `llama-3.3-70b-versatile`).
`LAB_PLANNER` selects the initial provider; `LAB_MODEL` is a legacy fallback for
that provider only. A missing selected key shows a configuration error with no
provider fallback. Keys remain in `.env`; never paste them into chat.
OpenAI routing uses the SDK Responses API with parsed structured output and
`store=False`. Groq and xAI Grok are different services.

Every submitted authenticated chat turn invokes one structured routing LLM call,
including patient-detail replies, corrections and booking confirmations. There is
no runtime keyword router or demo fallback. The existing LangGraph workflow
validates and executes up to four actions, with at most three tool steps per action.
Calls have a 15-second timeout, zero retries and a 1600-token output limit.
Mixed information and booking requests retain both tasks; read-only answers precede
the final booking clarification/read-back. Unknown actions, malformed arguments,
low confidence and provider failures execute no booking writes.

Patient names, DOBs, records and session credentials are excluded from routing
context. Patient replies are replaced locally with an identity placeholder before
classification; matching uses the original text locally. Known demographic values,
labelled identifiers and date strings are redacted. Laboratory queries retain full
test names and use the existing LabRAG API, whose privacy guard blocks demographic
queries. Do not include unlabelled personal information in laboratory questions;
this local protection is not a general-purpose DLP system.

Application code retains authorization, date/availability validation, database
summaries, explicit final booking confirmation and transactional deduplication.
Only a raw `confirm`, `yes`, or `yes confirm` (with punctuation allowed), classified
as confirmation by the LLM, can submit an existing exact-detail confirmation token.
Routing metadata (intent/tool, count, latency, failure category) stays in collapsed
activity output. Streamlit rerenders do not resubmit chat input.

All tests use mocked routing/providers and HTTP. No live routing call was made for
this change. See [every-turn routing](docs/every-turn-routing.md).

Working locally: appointment storage, patient lookup, daily overview, available
slots, appointment lookup, exact-detail confirmation, transactional booking,
idempotent retries, conversation state, tool traces, and text UI.

LabRAG uses the real local API at port 8000. Start it before Lab Agent; see
[local API setup and verification](docs/local-labrag-connection.md). No runtime mock fallback exists.
ElevenLabs has a transport boundary only; no speech service is connected.

## Architecture

`app/agents/lab_assistant.py` owns a single compiled StateGraph:
`plan → tool → evaluate → tool or end`. Each turn is bounded to three tools and
twelve graph steps; no automatic network retries. Booking first finds the patient,
evaluates the result, then prepares a readback. Clarification and slot conflicts
resume through subsequent user turns. State contains history, patient/test/date,
slots, selection, confirmation, request ID, and structured tool results. The UI and
reviewed voice flow use the same `LabAssistant.turn(message, state)` backend.
Voice has isolated state and an information-only routing/tool boundary; it never
passes a transcript into the text booking conversation.

`app/appointments/` owns all appointment logic:

- `models.py`: immutable typed requests, domain entities and structured results.
- `repository.py`: SQLite schema, SQL, queries and idempotent synthetic seeds.
- `service.py`: timezone validation, 30 minute slots, summaries, confirmation and booking.
- `gateway.py`: `AppointmentGateway.execute(GatewayRequest) -> Result` protocol and direct implementation.
- `tools.py`: thin callable around that protocol, reusable for future agent tools.
- `access.py`: trusted application access context, separate from model requests.
- `presentation.py`: deterministic public responses and six-column staff tables.

Booking uses one 30-minute appointment per staff member per slot, inside laboratory
hours 09:00–17:00 and that staff member's configured working hours. Concurrent
appointments with different staff are supported. Holidays, leave, cancellations,
rescheduling and clinical duration rules are outside this demo. All recorded statuses reserve their slots.
There are no diagnosis or treatment recommendations.

`prepare` validates and reads back patient name, test name, assigned staff, date, time and
timezone. Its random confirmation capability expires after ten minutes and binds
the canonical details, assigned staff, request ID and authenticated login session in SQLite. Only an exact user `confirm`,
`yes` or `yes confirm` turn submits it to `book`. A new `book` command starts fresh;
follow-up dates/times/tests change the pending proposal and obtain a new readback.
The UI activity panel shows operational status only. Staff-facing workflows
collect the patient's full name and DOB and resolve identifiers internally. All
users log in with a staff account; an ordinary overview shows only their assigned
appointments. An organization-wide overview requires a separate Admin capability.
Logout/session expiration clears private context. Name/DOB is demographic matching,
not authentication. The previous shared demo-admin token is no longer accepted.
Production still requires appropriate identity, auditing and deployment controls.
A future model must never be allowed to
mint user confirmation or directly access the service's confirmation capability.

`book` checks the stored confirmation and expiration, revalidates availability,
then inserts under `BEGIN IMMEDIATE`. Database uniqueness on assigned staff/slot and request
ID protects races and retries. Success is returned only after commit. An exact retry
reuses the persisted booking internally, including after the confirmation expires; different
details with that request ID fail. If a slot is lost, alternatives are returned and
the conversation clears selection/confirmation. On an uncertain persistence error,
retry the same conversation/request ID. Do not start a fresh booking to retry.

## Local LabRAG API

Start the LabRAG API from its own project root first:

```powershell
.venv/Scripts/python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Then start Lab Agent using its existing Streamlit command above. Defaults are
`LABRAG_BASE_URL=http://127.0.0.1:8000` and `LABRAG_TIMEOUT_SECONDS=90`.
The verified `/api/v1/query` contract accepts only a laboratory question and returns
status, answer, citations, matched tests and request ID. LabRAG owns retrieval,
embeddings, Pinecone and answer generation. Lab Agent handles clarification,
privacy-safe question context, citation display and distinct service failures.
There are no automatic query retries or mock fallbacks. Printed catalog, PDF and
original-PDF page fields remain separate. See the [connection report](docs/local-labrag-connection.md)
for schemas, error behavior, offline tests and the single successful live UI check.

Knowledge questions now pass full test names and qualifiers through verbatim,
including tests outside the appointment demo catalog. The earlier local allowlist
restriction is removed; see the [free-form clarification fix and direct/replay evidence](docs/freeform-labrag-clarification-fix.md).

## Future Scheduling Agent

Do not move appointment rules into prompts. Implement the same `AppointmentGateway`
protocol in a `SchedulingAgentGateway`, then replace only the gateway wiring in
`app/bootstrap.py`. The existing service/repository/tools remain unchanged.

The gateway's `GatewayRequest` carries the action, patient identifier, test identity,
requested date, `details.start` (selected timezone-aware slot), confirmation token
and request ID. `Result` carries `ok`, a machine-readable code, message, patient,
appointments, slots, persisted booking or confirmation-required result. A scheduling
agent can clarify or select tools, but a trusted adapter must carry the confirmation
state/capability from the original user interaction, enforce iteration limits and
call the same service booking guard. It must not infer consent from a prompt or tool
response. Do not generate a new request ID on a retry. Use the same contract tests
against the replacement gateway. No second reasoning agent is implemented now.

## Demo script

Startup seeds today's date with SYN001/Alex Demo at 09:00 (scheduled) and
SYN002/Jordan Sample at 10:00 (completed). SYN003/Taylor Example is also available.
Seeding is idempotent and does not overwrite existing records. Use a new temporary
`LAB_DATABASE` to reset the demo without deleting an existing database.

Existing records are preserved during migration: new fields remain NULL, including
DOB. Do not assume fixture DOBs apply to those records. Use verified demographic
maintenance to populate them, or use a new `LAB_DATABASE` path for a fresh synthetic
demo. The original database is `data/labassist.sqlite3`; it was not renamed to the
illustrative `lab_assist.sqlite3` filename. A schema upgrade automatically backs up
an existing database before adding nullable fields. To migrate without seeding, run
`.venv/Scripts/python.exe -m scripts.migrate_demographics`.

1. Run `python -m scripts.setup_staff` to set the local passwords. Sign in as Riley
   and ask `Show my schedule today` — only Riley's assignments appear. Sign in as
   Morgan and ask `organization-wide overview today` to inspect unassigned legacy rows.
2. `specimen blood` — choose `CBC` from the clarification. Point out the synthetic
   returned answer/status and source citations from the real local API.
3. As staff, ask `Schedule CBC 2030-01-15 at 1 PM with Jordan` (use a future configured workday). In a fresh demo database,
   answer `Taylor Example, June 10, 1995` when asked for full name and DOB.
4. Inspect the patient/test/date/time readback. Enter `11:30` to show changed details
   and a new confirmation. Enter `Yes, confirm.` to persist the appointment.
5. Repeat `confirm` to demonstrate a retry without another appointment. Log in as
   Jordan and ask for that day's schedule to see the assigned visit.
6. Start another `Schedule CBC 2030-01-15 at 11:30 with Jordan` to demonstrate alternatives;
   select a free time, review and confirm again. Expand Tool activity along the way.

## Voice boundary

Phase 1 ElevenLabs voice is available behind the sidebar's **Enable voice** toggle
(off by default). Record a synthetic general lab question, explicitly transcribe,
review/edit, and Send. The existing LabRAG workflow answers with sources; background
TTS provides replayable audio. Voice cannot book, confirm, access schedules, or
look up patients, and cannot change pending text bookings. The old automatic-submit
`app/voice/adapter.py` stub is disabled. See [configuration, startup, boundaries, and
browser acceptance checks](docs/elevenlabs-phase1.md).

Live ElevenLabs conversation is a separate Start/End control using the saved
Agent ID and server-minted signed URLs. Its read-only `lookup_lab_test` webhook
runs on port 8002 and delegates to the existing LabRAG API. Complete the
[dashboard tool, authentication, prompt, and HTTPS setup](docs/live-voice.md)
before starting; typed chat and reviewed voice remain available.

## Verification

Tests cover seeded summaries, missing fields, ambiguity, changed details, expiration,
confirmation, slot loss, parallel competing bookings, concurrent duplicate retries,
identity validation, demographic migration and preservation, staff authorization,
patient-scoped retrieval, natural-language presentation, missing references, HTTP errors and LabRAG timeouts. The UI
does not calculate slots or write SQL. Synthetic content is not clinical guidance.

LangGraph construction follows its [StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph).
The optional Groq provider uses [ChatGroq structured output](https://reference.langchain.com/python/langchain-groq/chat_models/ChatGroq/with_structured_output).
