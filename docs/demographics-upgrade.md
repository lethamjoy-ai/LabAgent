# Patient demographics and appointment workflow upgrade

This extends the existing application. The appointment transaction, availability
rules, request IDs, confirmation tokens and SQLite data remain in place. No LabRAG,
Pinecone, evaluation or provider credentials were changed. No live model/API calls
were used for verification, and nothing was pushed to GitHub.

## Database migration

The actual existing database is `data/labassist.sqlite3`, rather than the illustrative
`lab_assist.sqlite3` in the request. It remains at the original configured location.
Seven nullable TEXT columns were added to `patients`: `date_of_birth`, `gender`,
`address_line1`, `city`, `state`, `country`, `postal_code`. DOB uses YYYY-MM-DD;
postal codes retain leading zeros and international formats. No age column exists:
`Patient.age_on(date)` calculates age at any requested date, and `Patient.age`
calculates it dynamically for today.

Pre-migration SQLite backup:
`data/backups/labassist.before-demographics.20261107T205001874524Z.sqlite3`.
The backup uses SQLite's backup API, rather than copying a potentially active file.
The migration is transactional and checks existing columns before adding them;
repeating it does not add columns again or modify demographic values.

Migration result: 3 patient IDs/names, 3 entire appointment records and 1 entire
confirmation record preserved. Before/after SHA-256 comparison matched, and SQLite
integrity check returned `ok`. Details are in `data/demographics-migration-report.json`.
The script `python -m scripts.migrate_demographics` checks preservation without seeding.
An existing migration report retains its original backup reference on rerun.

Existing demographics were intentionally left NULL. No DOB was inferred from a
patient name or from example text. Consequently these existing patients cannot yet
match by name/DOB. Populate their DOB through authorized maintenance using verified
values; this release does not implement a registration or demographic editor.
For an immediate synthetic walkthrough, use a new database path such as
`LAB_DATABASE=data/demographics-demo.sqlite3` and restart the app. New fixture rows
only have these explicitly synthetic DOBs:

| Name | Synthetic DOB |
|---|---|
| Alex Demo | 1990-01-15 |
| Jordan Sample | 1988-04-20 |
| Taylor Example | 1995-06-10 |

## Patient matching, routing and scheduling

Names are normalized with Unicode NFKC, case folding and whitespace normalization.
The query uses bound SQL parameters and requires the full name and validated DOB.
An exact single match resolves the internal patient ID. Duplicate names with different
DOBs can be distinguished; duplicate name/DOB pairs, missing DOB and unknown people
receive clarification or staff referral without revealing candidate records.

The LangGraph plan/tool/evaluate loop now distinguishes booking, patient appointment
retrieval, staff overview, availability, confirmation/cancel and LabRAG questions.
A matching tool result can lead to retrieval or booking preparation in the same
bounded graph turn. DOB input never overwrites the requested appointment date.
Retrieval requests never ask for a test or appointment time and do not prepare a booking.
Upcoming queries include future scheduled visits; completed and historical filters
are separate, and all results sort chronologically. Exact dates filter by lab timezone.

Booking accepts requests such as `Schedule a CBC test tomorrow at 1 PM`, asks for
full name and DOB, checks the existing service, and reads back names and readable
date/time before confirmation. `Yes, confirm.` is recognized from raw user input;
model output cannot grant consent. Modified details get a new readback/token, slot
conflicts offer alternatives, and retries reuse the persisted booking. Internal IDs
are no longer shown in public replies, including successful booking replies. Staff
compatibility commands can still accept legacy IDs without printing them.

Deterministic formatters use actual database results for patient name, full test
name, date, time and status. They distinguish upcoming and historical visits, avoid
raw JSON and do not need a model call for standard appointment operations.

## Staff overview and privacy boundary

Patient mode is the default. `DirectAppointmentGateway` checks a trusted
`AccessContext` outside tool arguments and prompts. It rejects all staff overview
requests from patient sessions and scopes lookups, retrieval and bookings to the
matched patient. A different requested patient cannot bypass this boundary.

To test staff mode, set your own `LAB_DEMO_ADMIN_TOKEN` in `.env`, restart, and enter
it in the sidebar. Missing/incorrect tokens cannot enable staff mode. Staff mode
uses a demo-admin role; this is a synthetic local demo, not a production login.
Changing roles or starting a new conversation creates a fresh patient binding and
clears private history. A real deployment needs staff/patient authentication,
authorization, auditing, session lifecycle controls and protected transport/storage.
`AccessContext(synthetic_matching=False)` disables demographic matching as an access
mechanism; an authenticated patient subject must then be supplied by the application.
Name and DOB alone are never sufficient production authentication.

Staff overview shows a native six-column Streamlit table: Patient Name, Date of Birth,
Laboratory Test, Appointment Date, Appointment Time and Status. Missing DOB reads
`Not available`. Counts derive from actual appointments above the table; available
slots appear separately below. The table remains in conversation history on later
turns. Patient IDs, appointment primary keys, request IDs, internal test codes, SQL,
JSON, graph state and address fields are excluded. The collapsed activity panel
contains operation labels/status only, not sensitive tool arguments or results.

## Files changed

- `app/appointments/models.py`: demographics, dynamic age, display rows and typed requests.
- `app/appointments/repository.py`: backup/migration, normalized matching, patient queries and non-overwriting seeds.
- `app/appointments/service.py`: matching/retrieval, presentation rows and readable confirmation.
- `app/appointments/gateway.py`: trusted access checks and patient scoping.
- `app/appointments/access.py`, `presentation.py`: access context and deterministic presentation.
- `app/agents/lab_assistant.py`, `state.py`, `parsing.py`, `planner.py`: routing, identity follow-ups, date/time parsing and response formatting.
- `app/bootstrap.py`, `app/ui/streamlit_app.py`, `.env.example`: access wiring, token login, native table and redacted activity.
- `scripts/migrate_demographics.py`: repeatable migration/preservation report.
- `tests/conftest.py`, `test_agent.py`, `test_appointments.py`, `test_planner.py`, `test_ui.py`: existing regression coverage adapted to the new public workflow/access boundary.
- `tests/test_demographics.py`, `test_patient_workflows.py`: migration, matching, age, demographics, privacy and routing coverage.
- `README.md` and this report: setup, migration, walkthrough and remaining limitations.

## Manual verification

1. Open the app in patient mode. Ask `Show my upcoming appointments`. It must ask
   for full name and DOB, without requesting a test or time. In a fresh fixture
   database reply `Alex Demo, January 15, 1990`. Verify only Alex's actual visits appear.
2. Ask `Show my completed appointments` and `Do I have an appointment tomorrow?`.
   Confirm that status/date filters change retrieval without starting a booking.
3. Start a new conversation and ask `Schedule CBC tomorrow at 1 PM`. Reply with a
   full name and DOB from the fresh synthetic fixtures. Verify the readback contains
   full names/date/time/timezone and no internal IDs. Change time; confirm again.
   Say `Yes, confirm.` twice and verify only one additional appointment exists.
4. Request `daily overview today` in patient mode; access must be denied. Try a
   wrong admin token; it must stay denied. Enter the configured token and retry.
   Verify the six columns, status counts, `Not available` for a missing DOB and
   a separate available-slot list. Address data must never appear, even in Activity.
5. Ask `specimen blood`, select `CBC`, and confirm that LabRAG keeps its existing
   explicit mock label, reference fixture and missing preparation notice.

Run the complete offline suite with `.venv/Scripts/python.exe -m pytest -q`.
It covers original slot races/idempotency, migration/backup preservation, demographics,
age, full-name/DOB ambiguity, filters, readable replies, authorization and UI flows.
No external API calls are made. Final result: **33 passed** in 9.74 seconds.
The local server was restarted at `http://127.0.0.1:8501`; its health check returned
`ok`. Streamlit's simulated UI tests verified patient booking, staff token login,
table rendering, access denial and retained overview history. A Windows async
connection-reset callback was observed in the server log after restart; the health
endpoint and offline UI checks passed.

## Remaining limitations

No production authentication, demographic registration/editor, speech integration or
new LabRAG API was introduced. The existing LabRAG mock/configurable adapter remains
as before; no Pinecone index or evaluation artifact was touched. Name matching is
exact after normalization; no fuzzy identity guesses are made. Date parsing supports
ISO and English month-name dates, today/tomorrow, HH:MM and AM/PM. The optional Groq
planner remains available for unfamiliar initial phrasing without changing credentials.
Confirmation/cancel only affects pending requests; cancelling persisted appointments
still requires staff support. Laboratory hours, capacity and existing status/slot
rules remain the same.
