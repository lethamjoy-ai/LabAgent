# Appointment retrieval and availability display fix

## Root causes

1. `LabAssistant.route()` matched "my upcoming appointments" with a generic
   "my schedule/appointments" pattern and selected `overview`. That tool defaults
   to today, so it returned only today's future scheduled visits, not future
   appointments across dates.
2. The daily overview tool also calculated slots. The UI unconditionally rendered
   "Available Appointment Slots" for every overview table, including appointment
   retrieval. An empty slot result therefore produced the unrelated "No available
   slots" message. It did not indicate that the listed appointment was unavailable.
3. Availability phrases were checked after the generic "my appointments/schedule"
   route, allowing some slot questions to reach the wrong tool.

## Timestamp investigation (read-only)

The configured database is `data/labassist.sqlite3` and timezone is
`America/Los_Angeles`. A read-only SQLite query found the reported evening scheduled
record stored as **`2026-11-07T22:00:00-08:00`**. Local conversion leaves it at 22:00,
and `%I:%M %p` correctly displays **10:00 PM**. The other stored timestamps inspected
were `2026-11-07T13:00:00-08:00` (1 PM) and `2026-11-07T20:00:00-08:00` (8 PM).
No stored confirmation matched the 22:00 record's request to establish an intended
10 AM appointment. The 13:00 record's matching confirmation agreed with its time.

Consequently there is no verified 12-hour formatting or timezone-conversion bug
for the 22:00 record. The stored value represents 10 PM, but the original human
intent cannot be established from the available evidence. It must be checked with
an authorized operator before any correction. The current booking validator
rejects 22:00 as outside lab hours; that does not justify silently rewriting existing
data to 10 AM. **No existing appointment was modified.**

Before/after appointment-record fingerprint (all columns, sorted by ID):
`5f9dcb5ee11fa7d53c28277f176f30332cc23f74dfc60c102506222e1fda92ea`.
There were three records at inspection. Checks used SQLite read-only URI mode;
offline test data was confined to temporary databases.

## Changes

- Added a distinct `upcoming` LangGraph intent and `staff_appointments` gateway
  action. It queries only the authenticated staff member, then applies a strict
  future-datetime filter and scheduled status. No implicit "today" restriction is
  applied. An explicitly supplied date remains an optional extra filter.
- Kept daily schedule and Admin-only organization overview as separate date-scoped
  actions. Admin's normal upcoming request still shows only their own assignments.
  Other staff's full schedules remain inaccessible through upcoming retrieval.
- Gave explicit availability phrases priority over generic schedule patterns.
  Availability alone calls the existing slot calculation, using configured shifts,
  active staff, per-staff bookings, future times and existing unassigned holds.
  With the existing common 30-minute visit rules, an unspecified appointment type
  uses the service's CBC default; an unspecified date is requested explicitly.
- Daily overview no longer calculates or returns availability. Its display contains
  the schedule table and actual appointment/status counts only.
- Added a six-column, multi-date appointment table: Patient Name, Date of Birth,
  Laboratory Test, Appointment Date, Appointment Time and Status. Values use the
  configured laboratory timezone. Missing DOB displays `Not available`.
- Chat messages now carry separate `appointment_rows`, `overview_rows`, or
  `availability_slots` presentation payloads. The UI displays a slot section only
  when the message explicitly contains an availability result. Existing legacy
  `overview_slots` message fields no longer trigger the section.
- Upcoming summaries count exactly the returned rows, including zero matches.
  No internal IDs or addresses appear in the public tables/messages.
- Explicitly switching away from an unfinished booking clears the DOB-waiting
  flag, so a later schedule-date follow-up is not mistaken for a birth date.
- The optional model planner has a distinct upcoming intent, while normal
  appointment requests stay deterministic and require no model call.

Files changed: `app/agents/{lab_assistant,state,planner}.py`,
`app/appointments/{models,service,gateway,presentation}.py`,
`app/ui/streamlit_app.py`, `tests/test_appointments.py`,
`tests/test_patient_workflows.py`, `tests/test_ui.py`, and
`tests/test_appointment_responses.py`; this is a new report.

Pinecone, LabRAG integration, existing appointment records and historical reports
were not changed. No live Groq/OpenAI/Pinecone or other external API calls were made.

## Offline verification

Run `.venv/Scripts/python.exe -m pytest -q`.

Final result: **62 passed in 23.58 seconds**. The local application was reloaded;
the appointment-record fingerprint remained identical to the initial inspection.

Regression coverage includes future visits on different dates, exclusion of past,
completed and cancelled visits, own-staff filtering, Admin boundaries, daily
schedule scoping, explicit shift/booking-based availability, and Streamlit rendering
without unrelated slot sections. Time tests cover 10 AM versus 10 PM, UTC conversion,
local-date rollover and summer/winter offsets. Tests preserve stored evening values
instead of reinterpreting them as morning appointments.

Manual checks after signing in:

1. `Show my upcoming appointments.` — all future scheduled visits assigned to you,
   with six columns and no availability section.
2. `Show my daily schedule today.` — today's assigned visits and status counts,
   with no availability section.
3. `Available CBC slots 2030-01-15 with Jordan` — free times calculated from Jordan's
   shifts and bookings, displayed in a separate availability message. Use a suitable
   future date for your configured shifts.
4. `Organization-wide daily overview today` — denied for Staff; permitted for Admin.

Previously rendered messages remain in conversation history. Start a new
conversation if you want to inspect a single result without earlier messages.
