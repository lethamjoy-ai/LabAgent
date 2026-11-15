# Multi-staff login and scheduling

The person using chat or the future voice transport is a laboratory staff member.
Login is mandatory before the assistant, patient matching, staff availability or
LabRAG can be accessed. The prior patient/demo-token login is no longer supported;
`LAB_DEMO_ADMIN_TOKEN` does not grant access. Provider environment configuration and
the existing LabRAG adapter remain in place.

## Secure local setup

From the project root in a normal interactive PowerShell terminal:

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[dev,model]'
.venv/Scripts/python.exe -m scripts.setup_staff
.venv/Scripts/python.exe -m streamlit run streamlit_app.py
```

The three synthetic accounts are `riley.demo` (Riley Demo, Staff), `jordan.demo`
(Jordan Demo, Staff), and `morgan.demo` (Morgan Demo, Admin). The existing database
has these three accounts provisioned **inactive**. Their initial random Argon2
passwords were discarded; no default credentials or plaintext password file exists.
The setup command privately prompts for a password and repeat entry for each
inactive account. Supply at least 12 characters. The account activates only after
matching input. Passwords are never accepted through command arguments or written
to logs. If hidden terminal input is unavailable, setup fails rather than echoing.
An already active account is not overwritten by a normal setup rerun.

To deliberately reset an account password and revoke its previous sessions:

```powershell
.venv/Scripts/python.exe -m scripts.setup_staff --set-password riley.demo
```

The setup command is an OS-authorized local maintenance operation. Protect shell
access and database files; it is not exposed as a public signup endpoint. Login
and password reset use the [Argon2 PasswordHasher API](https://argon2-cffi.readthedocs.io/en/stable/howto.html),
including password verification and rehash checks. Staff DTOs never include hashes.

## Sessions and authorization

The login screen uses a password field and clears form fields on submission.
Invalid credentials/inactive accounts share an error message. Unknown usernames
also take a dummy password verification path. Successful login returns a random
opaque credential; only its SHA-256 digest is stored in `staff_sessions`. The
credential and a public staff DTO live in that Streamlit session, never graph/model
arguments. Default lifetime is 1,800 seconds, configurable with `LAB_SESSION_SECONDS`.
This is a fixed maximum lifetime, not a sliding idle timeout.

Every UI rerun, assistant turn, gateway call and booking operation rechecks the
current active account/session. Booking checks again inside its write transaction.
Logout deletes the session and clears assistant, selected patient, history, pending
confirmation and staff state. A staff switch requires logout/login and gets a fresh
conversation. The backend also discards context passed from a different login
session. Browser content already displayed can remain visible until rerun; expired
credentials cannot perform another operation.

`AccessContext` holds only the server-provided session credential. Staff identity and
role are loaded from SQLite, not accepted from tools, prompts or an LLM-generated
staff ID. Ordinary staff can view only their assigned schedule. Booking for Jordan
does not authorize viewing Jordan's schedule. Only an authenticated Admin can invoke
the separate `admin_overview` capability for organization-wide data, including
unassigned legacy appointments. Even Admin's ordinary "my schedule" stays scoped
to their own assignments. Patient-specific retrieval/booking-reference lookup is
also scoped to the logged-in staff member; selecting a patient does not grant
access to a colleague's assigned appointments.

## Migration and data preservation

The configured existing database remains `data/labassist.sqlite3`.

Backup: `data/backups/labassist.before-staff.20261107T234504178853Z.sqlite3`.
Report: `data/staff-migration-report.json`.

Migration preserved all original fields in **3 patient records, 3 appointment
records and 2 confirmation records**. Before/after record hashes matched. SQLite
integrity check returned `ok`, and foreign-key check returned zero errors.
The historical demographics backup/report remain untouched.

Changes:

- `staff`: internal ID, unique case-insensitive username, Argon2 password hash,
  first/last names, constrained Staff/Admin role and active flag.
- `staff_hours`: one weekly start/end window per staff/day, with a staff foreign key.
- `staff_sessions`: hashed credential, staff foreign key, creation/expiration times.
- `appointments.staff_id`: nullable foreign key to staff. A transactional table
  migration replaces global `UNIQUE(start)` with unique `(staff_id,start)` for
  assigned records. Existing appointment IDs, times, statuses and request IDs stay
  exactly as stored; legacy assignments remain NULL.
- `confirmations`: nullable `actor_staff_id` foreign key and `session_hash` bind new
  readbacks to the staff login that requested them. Original tokens, details,
  request IDs and expiration values remain intact. Legacy readbacks lacking an
  assignment/session need a fresh review before a new staff booking can be confirmed.

`python -m scripts.migrate_staff` repeats the checked migration without seeding.
It retains the original report/backup reference on rerun. Migrations are idempotent;
new patient demographics are still not guessed or overwritten.

## Availability and confirmation

Each active staff member has independent capacity for one 30-minute visit per slot.
Two staff members can serve two appointments at the same time. The laboratory's
09:00–17:00 rule is intersected with the selected staff member's working hours.
No working-hours entry means unavailable; inactive accounts are never offered.
For unresolved legacy records, an unassigned occupied slot conservatively blocks
that time for everyone until an authorized operator explicitly reconciles the
assignment. The migration never guesses which staff member performed a visit.

Initial synthetic shifts are in `config/demo_staff.json`: Riley and Jordan work
Monday–Friday 09:00–17:00; Morgan works Monday–Friday 10:00–16:00. These are explicit
demo fixtures, not an assertion about real staff availability. To change them,
edit the configuration and apply it locally:

```powershell
.venv/Scripts/python.exe -m scripts.setup_staff --apply-hours
```

This explicitly replaces configured weekly shifts, including removed weekdays,
without changing passwords or appointments. Weekdays use Monday=0 through Sunday=6.
Hours must use ordered 30-minute boundaries. Staffing exceptions, leave, holidays,
multiple shifts and automatic reconciliation of legacy assignments are not modeled.

When no assignee is named, the gateway prefers the logged-in staff member if
available, otherwise selects an available colleague deterministically. A `with
Jordan` request resolves an active first/full staff name; ambiguity asks for the
full name. If that person is unavailable, alternatives name available staff/times.
No alternate assignment is booked silently: selection receives a new readback.

Confirmation binds patient, test, timezone-aware time, assigned staff, request ID,
acting staff and login session. Exact raw user confirmation is required. A changed
patient/time/test/assignee invalidates the readback. A write transaction, fresh
session/availability checks, a per-staff slot constraint and request-ID uniqueness
protect concurrent bookings and retries. Success is returned only after commit.
Retrying within the original valid login returns the same persisted appointment.
An expired/revoked login must sign in again and review a fresh request.

## Conversation, UI and shared knowledge

The existing single LangGraph plan/tool/evaluate workflow remains bounded to three
tool executions per turn. Staff schedule questions never start a patient booking:

- "Show my schedule today."
- "What appointments do I have tomorrow?"
- "Show my completed appointments."
- "How many patients am I seeing today?"

The default schedule date is today unless a date is supplied. Status/upcoming/past
filters operate on that selected day. A staff-only table shows Patient Name, Date
of Birth, Laboratory Test, Appointment Time and Status, with the selected date in
the summary. Missing DOB displays `Not available`. Available slots appear below
the booked-appointment table; chat history retains earlier tables. IDs, addresses,
passwords, hashes, confirmation capabilities and raw tool/state JSON are excluded.
The collapsed activity panel shows operation names/outcomes only.

Booking asks "What is the patient's first name, last name, and date of birth?"
Names/DOB match a record; they do not authenticate a patient or replace staff login.
Responses address staff and identify the patient and assigned colleague. Routine
appointment summaries use deterministic formatting without extra model calls.

LabRAG remains shared across authenticated accounts: BUN specimen, Calcium minimum
volume and C-Peptide preparation requests route to the same configured client,
without adding staff identity to the retrieval question. These aliases extend
reference lookup, not the appointment-type catalog. The existing LabRAG client
default is still an explicitly labeled synthetic mock; a live API contract remains
unavailable. No Pinecone index or ingestion was modified. Test identity, source
references, missing-instruction reporting and HTTP failure behavior are retained.
The existing Groq environment configuration and optional planner remain intact.
There was no OpenAI provider selector in this LabAgent repository to modify; the
neighboring LabRAG project, its provider choices/citation pipeline, evaluation data,
checkpoints and reports were not changed. No live Groq/OpenAI/Pinecone requests were
made during development or tests.

## Manual demo

1. Run local setup to set the three passwords. Open `http://127.0.0.1:8501` and
   sign in as Riley. Confirm chat is inaccessible before login and incorrect
   credentials fail. Ask for today's schedule; only assigned visits are shown.
2. Existing patient DOBs remain NULL. For an isolated immediately usable synthetic
   walkthrough, use a new `LAB_DATABASE` path in `.env`, run staff setup against
   that path, then restart. On first authenticated app startup, new patient fixtures
   include Alex Demo (1990-01-15), Jordan Sample (1988-04-20) and Taylor Example
   (1995-06-10). This does not overwrite the original database.
3. Choose a future configured workday (e.g. `2030-01-15`). Ask
   `Schedule CBC 2030-01-15 at 11:00 with Jordan`. Supply
   `Alex Demo, January 15, 1990`. Check patient, full test, date/time/timezone and
   Jordan in the readback. Say `Yes`. Repeat `Yes` and verify no duplicate visit.
4. Ask `Show Jordan's schedule 2030-01-15` while logged in as Riley: access must be
   denied. Ask `Show my schedule 2030-01-15`: Riley must not see Jordan's new visit.
   Logout, sign in as Jordan, and ask again to see the assignment. Prior staff
   conversation must be absent.
5. Schedule another patient at the same time with Riley. Both bookings may exist.
   Try a second booking with Jordan at that time: named alternatives should appear,
   and a new selection/readback/confirmation must precede booking.
6. Sign in as Morgan and request `organization-wide overview 2030-01-15`. Ordinary
   staff making that request must be denied. A normal "my schedule" request by
   Morgan still shows only Morgan's assignments.
7. Ask each reference question (BUN, Calcium, C-Peptide) using either staff login;
   references and explicit mock/missing-instruction labels must be identical.
8. For an expiration walkthrough, set `LAB_SESSION_SECONDS=30`, restart, sign in,
   wait more than 30 seconds and interact again. Login returns and private
   conversation/pending confirmation are cleared.

## Files changed and verification

- `app/staff/{models,repository,auth}.py`, `__init__.py`: staff DTOs, account/hours/session persistence and Argon2 login.
- `app/appointments/{staff_migration,access,models,repository,service,gateway,presentation}.py`: backed-up schema, per-staff capacity, trusted session context, schedule scoping and staff-facing presentation.
- `app/agents/{lab_assistant,state,parsing,planner}.py`: staff intents, target staff selection, consent/session isolation and shared reference aliases.
- `app/ui/streamlit_app.py`, `app/bootstrap.py`, `.env.example`: mandatory login/logout, session wiring and configuration.
- `app/integrations/labrag_client.py`: synthetic reference names for the additional shared test aliases; HTTP contract unchanged.
- `config/demo_staff.json`, `scripts/setup_staff.py`, `scripts/migrate_staff.py`: local secure setup, shifts and preservation report.
- `scripts/migrate_demographics.py`: preserves original-column comparisons after additive staff columns.
- `pyproject.toml`: Argon2 dependency and explicit package discovery excluding runtime database files.
- `tests/{conftest,test_appointments,test_agent,test_planner,test_demographics,test_patient_workflows,test_ui,test_staff}.py`: authenticated regressions, migration, races, authorization, shared LabRAG, setup and UI tests.
- `README.md`, this report: current setup/demonstration and limitations. Historical documents remain as records of earlier behavior.

Run `.venv/Scripts/python.exe -m pytest -q` for all offline tests. They use temporary
databases, synthetic accounts and mocked external interfaces, with no live model
or retrieval API calls. Final verification: **44 passed in 41.13 seconds**. The
local server was restarted at `http://127.0.0.1:8501`, and its health check returned
`ok`. UI tests verified login, incorrect passwords, booking for a colleague,
assigned tables, preserved same-session chat history, logout/staff-switch isolation
and expiration hiding private context.

## Remaining security limitations

This is a local synthetic-data course demo, not a production healthcare deployment.
It does not provide MFA/SSO, account lockout/distributed rate limiting, a complete
audit trail, encrypted database storage, an admin web account manager or remote TLS
termination. Protect OS/database/backups and use a production identity/access and
audit design before introducing real patient data. Staff signup/password/hours
maintenance is local-terminal-only. There is no automatic assignment of legacy
visits or demographic backfill. The existing voice adapter remains a transport
boundary and must receive this authenticated staff context before voice is enabled.
One reasoning workflow remains; no additional voice or scheduling agent was added.
Nothing was uploaded to GitHub.
